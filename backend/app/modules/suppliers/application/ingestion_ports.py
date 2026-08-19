from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from app.modules.suppliers.extraction.models import (
    DocumentProcessingStatus,
    SourceDocumentDTO,
)


class BlobNotFoundError(LookupError):
    """Raised when a tenant cannot access a blob reference."""


class RepositoryConflictError(RuntimeError):
    """Raised when a document write observes an unexpected state."""


class StoredBlobDTO(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    blob_id: str
    tenant_id: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    media_type: str
    size_bytes: int = Field(gt=0)
    reused: bool


class ExtractionJobDTO(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    job_id: str
    tenant_id: str
    supplier_id: str
    document_id: str
    enqueued_at: datetime


class DocumentLifecycleEntryDTO(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    document_id: str
    status: DocumentProcessingStatus
    occurred_at: datetime


class ExtractionFailureDTO(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    document_id: str
    reason_code: str
    failed_at: datetime


class Clock(Protocol):
    def now(self) -> datetime: ...


class IdGenerator(Protocol):
    def new(self, prefix: str) -> str: ...


class DocumentStoragePort(Protocol):
    async def store(
        self,
        *,
        tenant_id: str,
        sha256: str,
        media_type: str,
        content: bytes,
    ) -> StoredBlobDTO: ...

    async def read(self, *, tenant_id: str, blob_id: str) -> bytes: ...


class SourceDocumentRepositoryPort(Protocol):
    async def add(
        self,
        document: SourceDocumentDTO,
        *,
        initial_statuses: Sequence[DocumentProcessingStatus],
        occurred_at: datetime,
    ) -> None: ...

    async def get(self, document_id: str) -> SourceDocumentDTO | None: ...

    async def transition(
        self,
        document_id: str,
        *,
        expected_status: DocumentProcessingStatus,
        new_status: DocumentProcessingStatus,
        occurred_at: datetime,
    ) -> SourceDocumentDTO: ...

    async def record_failure(self, failure: ExtractionFailureDTO) -> None: ...


class ExtractionQueuePort(Protocol):
    async def enqueue(self, job: ExtractionJobDTO) -> None: ...
