from __future__ import annotations

import asyncio
from collections import deque

from app.modules.suppliers.application.ingestion_ports import (
    ExtractionJobDTO,
    RepositoryConflictError,
)


class InMemoryExtractionQueue:
    """Deterministic queue supporting tenant-bound worker claims."""

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

    async def dequeue_matching(
        self,
        *,
        tenant_id: str,
        supplier_id: str,
        document_id: str | None,
    ) -> ExtractionJobDTO | None:
        async with self._lock:
            for job in self._jobs:
                if (
                    job.tenant_id == tenant_id
                    and job.supplier_id == supplier_id
                    and (document_id is None or job.document_id == document_id)
                ):
                    self._jobs.remove(job)
                    return job
            return None

    async def pending(self) -> list[ExtractionJobDTO]:
        async with self._lock:
            return list(self._jobs)
