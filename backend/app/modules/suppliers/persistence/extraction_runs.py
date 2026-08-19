from __future__ import annotations

import asyncio
from datetime import datetime

from app.modules.suppliers.application.extraction_service import (
    ExtractionEventDTO,
    ExtractionRunRecordDTO,
    ExtractionRunStatus,
)
from app.modules.suppliers.application.ingestion_ports import ExtractionJobDTO
from app.modules.suppliers.extraction.models import SupplierExtractionResultDTO


class InMemoryExtractionRunRepository:
    """Append-safe run/result fake isolated from review-owned field versions."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._runs: dict[str, ExtractionRunRecordDTO] = {}
        self._jobs: dict[str, ExtractionJobDTO] = {}
        self._latest_job_by_document: dict[tuple[str, str, str], str] = {}

    async def start(
        self,
        job: ExtractionJobDTO,
        *,
        started_at: datetime,
    ) -> ExtractionRunRecordDTO:
        async with self._lock:
            existing = self._runs.get(job.job_id)
            if existing is not None:
                if self._jobs[job.job_id] != job:
                    raise ValueError("extraction job id was reused with another payload")
                return existing
            record = ExtractionRunRecordDTO(
                job_id=job.job_id,
                tenant_id=job.tenant_id,
                supplier_id=job.supplier_id,
                document_id=job.document_id,
                status=ExtractionRunStatus.EXTRACTING,
                started_at=started_at,
            )
            self._jobs[job.job_id] = job
            self._runs[job.job_id] = record
            self._latest_job_by_document[(job.tenant_id, job.supplier_id, job.document_id)] = (
                job.job_id
            )
            return record

    async def complete(
        self,
        job_id: str,
        *,
        result: SupplierExtractionResultDTO,
        finished_at: datetime,
    ) -> ExtractionRunRecordDTO:
        async with self._lock:
            current = self._required_run(job_id)
            if current.document_id != result.document_id:
                raise ValueError("extraction result belongs to another document")
            if current.status is ExtractionRunStatus.COMPLETED:
                return current
            if current.status is not ExtractionRunStatus.EXTRACTING:
                raise ValueError("only an extracting run can complete")
            completed = current.model_copy(
                update={
                    "status": ExtractionRunStatus.COMPLETED,
                    "extraction_run_id": result.extraction_run_id,
                    "finished_at": finished_at,
                    "fields": [field.model_copy(deep=True) for field in result.fields],
                    "failure_code": None,
                }
            )
            self._runs[job_id] = completed
            return completed

    async def fail(
        self,
        job_id: str,
        *,
        failure_code: str,
        finished_at: datetime,
    ) -> ExtractionRunRecordDTO:
        async with self._lock:
            current = self._required_run(job_id)
            if current.status is ExtractionRunStatus.FAILED:
                return current
            if current.status is not ExtractionRunStatus.EXTRACTING:
                raise ValueError("only an extracting run can fail")
            failed = current.model_copy(
                update={
                    "status": ExtractionRunStatus.FAILED,
                    "finished_at": finished_at,
                    "fields": [],
                    "failure_code": failure_code,
                }
            )
            self._runs[job_id] = failed
            return failed

    async def get_for_document(
        self,
        *,
        tenant_id: str,
        supplier_id: str,
        document_id: str,
    ) -> ExtractionRunRecordDTO | None:
        async with self._lock:
            job_id = self._latest_job_by_document.get((tenant_id, supplier_id, document_id))
            return self._runs.get(job_id) if job_id is not None else None

    def _required_run(self, job_id: str) -> ExtractionRunRecordDTO:
        try:
            return self._runs[job_id]
        except KeyError as error:
            raise KeyError(f"unknown extraction job {job_id}") from error


class InMemoryExtractionEventSink:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._events: list[ExtractionEventDTO] = []

    async def append(self, event: ExtractionEventDTO) -> None:
        async with self._lock:
            self._events.append(event)

    async def all(self) -> list[ExtractionEventDTO]:
        async with self._lock:
            return list(self._events)
