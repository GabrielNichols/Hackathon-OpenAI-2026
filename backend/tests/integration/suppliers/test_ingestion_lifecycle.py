from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.modules.suppliers.application.ingestion import (
    InvalidDocumentTransitionError,
    SupplierMaterialIngestionService,
)
from app.modules.suppliers.application.ingestion_ports import ExtractionJobDTO
from app.modules.suppliers.extraction.models import DocumentProcessingStatus
from app.modules.suppliers.persistence.in_memory import (
    FakeExtractionQueue,
    InMemoryDocumentStorage,
    InMemorySourceDocumentRepository,
)


class FixedClock:
    def now(self) -> datetime:
        return datetime(2026, 8, 19, 15, tzinfo=UTC)


class SequenceIds:
    def __init__(self) -> None:
        self._counts: dict[str, int] = {}

    def new(self, prefix: str) -> str:
        self._counts[prefix] = self._counts.get(prefix, 0) + 1
        return f"{prefix}_{self._counts[prefix]}"


@pytest.fixture
def collaborators() -> tuple[
    SupplierMaterialIngestionService,
    InMemoryDocumentStorage,
    InMemorySourceDocumentRepository,
    FakeExtractionQueue,
]:
    storage = InMemoryDocumentStorage()
    documents = InMemorySourceDocumentRepository()
    queue = FakeExtractionQueue()
    service = SupplierMaterialIngestionService(
        storage=storage,
        documents=documents,
        extraction_queue=queue,
        clock=FixedClock(),
        ids=SequenceIds(),
        max_size_bytes=1_000_000,
    )
    return service, storage, documents, queue


@pytest.mark.asyncio
async def test_upload_records_sha256_original_metadata_and_lifecycle(
    collaborators: tuple[
        SupplierMaterialIngestionService,
        InMemoryDocumentStorage,
        InMemorySourceDocumentRepository,
        FakeExtractionQueue,
    ],
) -> None:
    service, _, documents, queue = collaborators

    uploaded = await service.ingest_file(
        tenant_id="org_1",
        supplier_id="sup_1",
        original_filename="../Catálogo.PDF",
        declared_media_type="application/pdf",
        content=b"%PDF-1.7\nfixture",
    )

    assert uploaded.document.document_id == "doc_1"
    assert uploaded.document.original_filename == "../Catálogo.PDF"
    assert uploaded.document.sanitized_filename == "Catálogo.pdf"
    assert uploaded.document.status is DocumentProcessingStatus.EXTRACTION_QUEUED
    assert uploaded.blob_reused is False
    assert [entry.status for entry in await documents.history("doc_1")] == [
        DocumentProcessingStatus.RECEIVED,
        DocumentProcessingStatus.VALIDATED,
        DocumentProcessingStatus.STORED,
        DocumentProcessingStatus.EXTRACTION_QUEUED,
    ]
    assert await queue.pending() == [
        ExtractionJobDTO(
            job_id="job_1",
            tenant_id="org_1",
            supplier_id="sup_1",
            document_id="doc_1",
            enqueued_at=FixedClock().now(),
        )
    ]


@pytest.mark.asyncio
async def test_duplicate_document_reuses_blob_without_losing_upload_reference(
    collaborators: tuple[
        SupplierMaterialIngestionService,
        InMemoryDocumentStorage,
        InMemorySourceDocumentRepository,
        FakeExtractionQueue,
    ],
) -> None:
    service, _, _, queue = collaborators
    content = b"%PDF-1.7\nsame"

    first = await service.ingest_file(
        tenant_id="org_1",
        supplier_id="sup_1",
        original_filename="first.pdf",
        declared_media_type="application/pdf",
        content=content,
    )
    second = await service.ingest_file(
        tenant_id="org_1",
        supplier_id="sup_1",
        original_filename="second.pdf",
        declared_media_type="application/pdf",
        content=content,
    )

    assert first.document.document_id != second.document.document_id
    assert first.document.blob_id == second.document.blob_id
    assert second.blob_reused is True
    assert len(await queue.pending()) == 2


@pytest.mark.asyncio
async def test_extraction_failure_keeps_original_document(
    collaborators: tuple[
        SupplierMaterialIngestionService,
        InMemoryDocumentStorage,
        InMemorySourceDocumentRepository,
        FakeExtractionQueue,
    ],
) -> None:
    service, _, documents, _ = collaborators
    content = b"\x89PNG\r\n\x1a\nfixture"
    uploaded = await service.ingest_file(
        tenant_id="org_1",
        supplier_id="sup_1",
        original_filename="menu.png",
        declared_media_type="image/png",
        content=content,
    )

    await service.mark_extraction_started(uploaded.document.document_id)
    failed = await service.mark_extraction_failed(
        uploaded.document.document_id,
        reason_code="PROVIDER_UNAVAILABLE",
    )
    retrieved, original = await service.get_material(
        tenant_id="org_1",
        supplier_id="sup_1",
        document_id=uploaded.document.document_id,
    )

    assert failed.status is DocumentProcessingStatus.EXTRACTION_FAILED
    assert retrieved.status is DocumentProcessingStatus.EXTRACTION_FAILED
    assert original == content
    assert (await documents.failure("doc_1")).reason_code == "PROVIDER_UNAVAILABLE"


@pytest.mark.asyncio
async def test_lifecycle_rejects_skipping_extraction_state(
    collaborators: tuple[
        SupplierMaterialIngestionService,
        InMemoryDocumentStorage,
        InMemorySourceDocumentRepository,
        FakeExtractionQueue,
    ],
) -> None:
    service, _, _, _ = collaborators
    uploaded = await service.ingest_text(
        tenant_id="org_1",
        supplier_id="sup_1",
        text="Atendemos a partir de 30 pessoas",
    )

    with pytest.raises(InvalidDocumentTransitionError):
        await service.mark_extraction_completed(uploaded.document.document_id)


@pytest.mark.asyncio
async def test_fake_queue_is_fifo_and_idempotent_per_job(
    collaborators: tuple[
        SupplierMaterialIngestionService,
        InMemoryDocumentStorage,
        InMemorySourceDocumentRepository,
        FakeExtractionQueue,
    ],
) -> None:
    _, _, _, queue = collaborators
    first = ExtractionJobDTO(
        job_id="job_1",
        tenant_id="org_1",
        supplier_id="sup_1",
        document_id="doc_1",
        enqueued_at=FixedClock().now(),
    )
    second = first.model_copy(update={"job_id": "job_2", "document_id": "doc_2"})

    await queue.enqueue(first)
    await queue.enqueue(first)
    await queue.enqueue(second)

    assert await queue.dequeue() == first
    assert await queue.dequeue() == second
    assert await queue.dequeue() is None
