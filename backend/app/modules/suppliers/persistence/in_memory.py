from __future__ import annotations

import asyncio
import hashlib
from collections import deque
from collections.abc import Sequence
from datetime import datetime

from app.modules.suppliers.application.ingestion_ports import (
    BlobNotFoundError,
    DocumentLifecycleEntryDTO,
    ExtractionFailureDTO,
    ExtractionJobDTO,
    RepositoryConflictError,
    StoredBlobDTO,
)
from app.modules.suppliers.extraction.models import (
    DocumentProcessingStatus,
    SourceDocumentDTO,
)


class InMemoryDocumentStorage:
    """Tenant-scoped, content-addressed storage fake with the production contract."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._blob_by_key: dict[tuple[str, str], StoredBlobDTO] = {}
        self._content_by_blob_id: dict[str, bytes] = {}

    async def store(
        self,
        *,
        tenant_id: str,
        sha256: str,
        media_type: str,
        content: bytes,
    ) -> StoredBlobDTO:
        actual_sha256 = hashlib.sha256(content).hexdigest()
        if actual_sha256 != sha256:
            raise ValueError("content does not match declared sha256")
        key = (tenant_id, sha256)
        async with self._lock:
            existing = self._blob_by_key.get(key)
            if existing is not None:
                if self._content_by_blob_id[existing.blob_id] != content:
                    raise ValueError("sha256 collision detected")
                return existing.model_copy(update={"reused": True})

            identity = hashlib.sha256(f"{tenant_id}:{sha256}".encode()).hexdigest()[:24]
            blob = StoredBlobDTO(
                blob_id=f"blob_{identity}",
                tenant_id=tenant_id,
                sha256=sha256,
                media_type=media_type,
                size_bytes=len(content),
                reused=False,
            )
            self._blob_by_key[key] = blob
            self._content_by_blob_id[blob.blob_id] = bytes(content)
            return blob

    async def read(self, *, tenant_id: str, blob_id: str) -> bytes:
        async with self._lock:
            blob = next(
                (
                    item
                    for (candidate_tenant, _), item in self._blob_by_key.items()
                    if candidate_tenant == tenant_id and item.blob_id == blob_id
                ),
                None,
            )
            if blob is None:
                raise BlobNotFoundError(blob_id)
            return bytes(self._content_by_blob_id[blob.blob_id])


class InMemorySourceDocumentRepository:
    """Append-observable fake repository used until the core UoW is merged."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._documents: dict[str, SourceDocumentDTO] = {}
        self._history: dict[str, list[DocumentLifecycleEntryDTO]] = {}
        self._failures: dict[str, ExtractionFailureDTO] = {}

    async def add(
        self,
        document: SourceDocumentDTO,
        *,
        initial_statuses: Sequence[DocumentProcessingStatus],
        occurred_at: datetime,
    ) -> None:
        if not initial_statuses or initial_statuses[-1] is not document.status:
            raise ValueError("initial lifecycle must end at the document status")
        async with self._lock:
            if document.document_id in self._documents:
                raise RepositoryConflictError(f"duplicate document {document.document_id}")
            self._documents[document.document_id] = document
            self._history[document.document_id] = [
                DocumentLifecycleEntryDTO(
                    document_id=document.document_id,
                    status=status,
                    occurred_at=occurred_at,
                )
                for status in initial_statuses
            ]

    async def get(self, document_id: str) -> SourceDocumentDTO | None:
        async with self._lock:
            return self._documents.get(document_id)

    async def transition(
        self,
        document_id: str,
        *,
        expected_status: DocumentProcessingStatus,
        new_status: DocumentProcessingStatus,
        occurred_at: datetime,
    ) -> SourceDocumentDTO:
        async with self._lock:
            document = self._documents.get(document_id)
            if document is None:
                raise KeyError(document_id)
            if document.status is not expected_status:
                raise RepositoryConflictError(
                    f"expected {expected_status}, found {document.status} for {document_id}"
                )
            updated = document.model_copy(update={"status": new_status})
            self._documents[document_id] = updated
            self._history[document_id].append(
                DocumentLifecycleEntryDTO(
                    document_id=document_id,
                    status=new_status,
                    occurred_at=occurred_at,
                )
            )
            return updated

    async def record_failure(self, failure: ExtractionFailureDTO) -> None:
        async with self._lock:
            self._failures[failure.document_id] = failure

    async def history(self, document_id: str) -> list[DocumentLifecycleEntryDTO]:
        async with self._lock:
            if document_id not in self._history:
                raise KeyError(document_id)
            return list(self._history[document_id])

    async def failure(self, document_id: str) -> ExtractionFailureDTO:
        async with self._lock:
            try:
                return self._failures[document_id]
            except KeyError as error:
                raise KeyError(f"no extraction failure for {document_id}") from error


class FakeExtractionQueue:
    """Deterministic FIFO fake; retries of the same job are idempotent."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._jobs: deque[ExtractionJobDTO] = deque()
        self._known_jobs: dict[str, ExtractionJobDTO] = {}

    async def enqueue(self, job: ExtractionJobDTO) -> None:
        async with self._lock:
            existing = self._known_jobs.get(job.job_id)
            if existing is not None:
                if existing != job:
                    raise RepositoryConflictError(
                        f"job id {job.job_id} was reused with another payload"
                    )
                return
            self._known_jobs[job.job_id] = job
            self._jobs.append(job)

    async def dequeue(self) -> ExtractionJobDTO | None:
        async with self._lock:
            if not self._jobs:
                return None
            return self._jobs.popleft()

    async def pending(self) -> list[ExtractionJobDTO]:
        async with self._lock:
            return list(self._jobs)
