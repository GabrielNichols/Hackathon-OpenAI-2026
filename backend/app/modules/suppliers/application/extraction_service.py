from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from app.modules.suppliers.application.ingestion import (
    MaterialNotFoundError,
    SupplierMaterialIngestionService,
)
from app.modules.suppliers.application.ingestion_ports import (
    Clock,
    ExtractionJobDTO,
    SourceDocumentRepositoryPort,
)
from app.modules.suppliers.extraction.models import (
    DocumentProcessingStatus,
    ExtractedFieldDTO,
    SupplierExtractionResultDTO,
    SupplierExtractionSchema,
)
from app.modules.suppliers.extraction.ports import SupplierExtractionPort
from app.modules.suppliers.extraction.provenance import finalize_extraction_result


class ExtractionRunStatus(StrEnum):
    EXTRACTING = "EXTRACTING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class ExtractionQueueEmptyError(LookupError):
    pass


class ExtractionResultNotFoundError(LookupError):
    pass


class ExtractionRunRecordDTO(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    job_id: str
    tenant_id: str
    supplier_id: str
    document_id: str
    status: ExtractionRunStatus
    extraction_run_id: str | None = None
    started_at: datetime
    finished_at: datetime | None = None
    fields: list[ExtractedFieldDTO] = Field(default_factory=list)
    failure_code: str | None = None


class ExtractionExecutionDTO(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    run: ExtractionRunRecordDTO
    document_status: DocumentProcessingStatus


class ExtractionEventDTO(BaseModel):
    """Audit-compatible event containing identifiers and stable codes only."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    event_type: str
    tenant_id: str
    supplier_id: str
    document_id: str
    job_id: str
    extraction_run_id: str | None
    occurred_at: datetime
    failure_code: str | None = None


class ExtractionQueueConsumerPort(Protocol):
    async def dequeue_matching(
        self,
        *,
        tenant_id: str,
        supplier_id: str,
        document_id: str | None,
    ) -> ExtractionJobDTO | None: ...


class ExtractionRunRepositoryPort(Protocol):
    async def start(
        self,
        job: ExtractionJobDTO,
        *,
        started_at: datetime,
    ) -> ExtractionRunRecordDTO: ...

    async def complete(
        self,
        job_id: str,
        *,
        result: SupplierExtractionResultDTO,
        finished_at: datetime,
    ) -> ExtractionRunRecordDTO: ...

    async def fail(
        self,
        job_id: str,
        *,
        failure_code: str,
        finished_at: datetime,
    ) -> ExtractionRunRecordDTO: ...

    async def get_for_document(
        self,
        *,
        tenant_id: str,
        supplier_id: str,
        document_id: str,
    ) -> ExtractionRunRecordDTO | None: ...


class ExtractionEventPort(Protocol):
    async def append(self, event: ExtractionEventDTO) -> None: ...


class SupplierExtractionService:
    def __init__(
        self,
        *,
        ingestion: SupplierMaterialIngestionService,
        documents: SourceDocumentRepositoryPort,
        queue: ExtractionQueueConsumerPort,
        provider: SupplierExtractionPort,
        runs: ExtractionRunRepositoryPort,
        events: ExtractionEventPort,
        clock: Clock,
        schema: SupplierExtractionSchema | None = None,
        confidence_threshold: float = 0.8,
    ) -> None:
        if not 0 <= confidence_threshold <= 1:
            raise ValueError("confidence_threshold must be between zero and one")
        self._ingestion = ingestion
        self._documents = documents
        self._queue = queue
        self._provider = provider
        self._runs = runs
        self._events = events
        self._clock = clock
        self._schema = schema or SupplierExtractionSchema.canonical()
        self._confidence_threshold = confidence_threshold

    async def process_next(
        self,
        *,
        tenant_id: str,
        supplier_id: str,
        document_id: str | None = None,
    ) -> ExtractionExecutionDTO:
        job = await self._queue.dequeue_matching(
            tenant_id=tenant_id,
            supplier_id=supplier_id,
            document_id=document_id,
        )
        if job is None:
            raise ExtractionQueueEmptyError("no queued extraction matches the request")
        document = await self._documents.get(job.document_id)
        if (
            document is None
            or document.tenant_id != job.tenant_id
            or document.supplier_id != job.supplier_id
        ):
            raise MaterialNotFoundError("queued material was not found")

        extracting_document = await self._ingestion.mark_extraction_started(job.document_id)
        record = await self._runs.start(job, started_at=self._clock.now())
        await self._events.append(self._event("SUPPLIER_EXTRACTION_STARTED", job, record=record))
        try:
            provider_result = await self._provider.extract(
                extracting_document,
                self._schema,
            )
            self._validate_provider_result(job.document_id, provider_result)
            finalized = finalize_extraction_result(
                provider_result,
                confidence_threshold=self._confidence_threshold,
            )
            record = await self._runs.complete(
                job.job_id,
                result=finalized,
                finished_at=self._clock.now(),
            )
            await self._ingestion.mark_extraction_completed(job.document_id)
            awaiting_review = await self._ingestion.mark_awaiting_supplier_review(job.document_id)
            await self._events.append(
                self._event("SUPPLIER_EXTRACTION_COMPLETED", job, record=record)
            )
            return ExtractionExecutionDTO(
                run=record,
                document_status=awaiting_review.status,
            )
        except Exception:
            failed_document = await self._ingestion.mark_extraction_failed(
                job.document_id,
                reason_code="PROVIDER_ERROR",
            )
            record = await self._runs.fail(
                job.job_id,
                failure_code="PROVIDER_ERROR",
                finished_at=self._clock.now(),
            )
            await self._events.append(self._event("SUPPLIER_EXTRACTION_FAILED", job, record=record))
            return ExtractionExecutionDTO(
                run=record,
                document_status=failed_document.status,
            )

    async def get_status(
        self,
        *,
        tenant_id: str,
        supplier_id: str,
        document_id: str,
    ) -> ExtractionExecutionDTO:
        record = await self._runs.get_for_document(
            tenant_id=tenant_id,
            supplier_id=supplier_id,
            document_id=document_id,
        )
        document = await self._documents.get(document_id)
        if record is None or document is None:
            raise ExtractionResultNotFoundError("extraction status was not found")
        return ExtractionExecutionDTO(run=record, document_status=document.status)

    @staticmethod
    def _validate_provider_result(
        document_id: str,
        result: SupplierExtractionResultDTO,
    ) -> None:
        if result.document_id != document_id:
            raise ValueError("provider result is bound to another document")
        if any(
            field.source_document_id != document_id
            or field.extraction_run_id != result.extraction_run_id
            for field in result.fields
        ):
            raise ValueError("provider field provenance does not match its extraction run")

    def _event(
        self,
        event_type: str,
        job: ExtractionJobDTO,
        *,
        record: ExtractionRunRecordDTO,
    ) -> ExtractionEventDTO:
        return ExtractionEventDTO(
            event_type=event_type,
            tenant_id=job.tenant_id,
            supplier_id=job.supplier_id,
            document_id=job.document_id,
            job_id=job.job_id,
            extraction_run_id=record.extraction_run_id,
            occurred_at=self._clock.now(),
            failure_code=record.failure_code,
        )
