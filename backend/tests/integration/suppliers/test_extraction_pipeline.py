from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest
from fastapi import FastAPI, Request

from app.modules.suppliers.api.extraction_router import create_extraction_router
from app.modules.suppliers.application.extraction_service import (
    ExtractionRunStatus,
    SupplierExtractionService,
)
from app.modules.suppliers.application.ingestion import SupplierMaterialIngestionService
from app.modules.suppliers.extraction.models import (
    DocumentProcessingStatus,
    ExtractionFieldStatus,
)
from app.modules.suppliers.extraction.providers import FakeSupplierExtractionProvider
from app.modules.suppliers.persistence.extraction_queue import InMemoryExtractionQueue
from app.modules.suppliers.persistence.extraction_runs import (
    InMemoryExtractionEventSink,
    InMemoryExtractionRunRepository,
)
from app.modules.suppliers.persistence.in_memory import (
    InMemoryDocumentStorage,
    InMemorySourceDocumentRepository,
)

NOW = datetime(2026, 8, 19, 15, tzinfo=UTC)


class FixedClock:
    def now(self) -> datetime:
        return NOW


class SequenceIds:
    def __init__(self) -> None:
        self._counts: dict[str, int] = {}

    def new(self, prefix: str) -> str:
        self._counts[prefix] = self._counts.get(prefix, 0) + 1
        return f"{prefix}_{self._counts[prefix]}"


def collaborators(
    *,
    provider_fixtures: dict[str, dict[str, dict[str, object]]],
) -> tuple[
    SupplierMaterialIngestionService,
    SupplierExtractionService,
    InMemoryExtractionRunRepository,
    InMemoryExtractionEventSink,
]:
    storage = InMemoryDocumentStorage()
    documents = InMemorySourceDocumentRepository()
    queue = InMemoryExtractionQueue()
    ingestion = SupplierMaterialIngestionService(
        storage=storage,
        documents=documents,
        extraction_queue=queue,
        clock=FixedClock(),
        ids=SequenceIds(),
        max_size_bytes=10_000,
    )
    runs = InMemoryExtractionRunRepository()
    events = InMemoryExtractionEventSink()
    extraction = SupplierExtractionService(
        ingestion=ingestion,
        documents=documents,
        queue=queue,
        provider=FakeSupplierExtractionProvider(
            fixtures=provider_fixtures,
            fixed_now=NOW,
        ),
        runs=runs,
        events=events,
        clock=FixedClock(),
    )
    return ingestion, extraction, runs, events


@pytest.mark.asyncio
async def test_queued_document_is_extracted_finalized_and_persisted() -> None:
    ingestion, extraction, runs, events = collaborators(
        provider_fixtures={
            "doc_1": {
                "minimum_people": {
                    "value": "30 pessoas",
                    "normalized_value": 30,
                    "confidence": 0.5,
                    "source_page": 1,
                    "source_excerpt": "Mínimo 30 pessoas",
                }
            }
        }
    )
    uploaded = await ingestion.ingest_file(
        tenant_id="org_1",
        supplier_id="sup_1",
        original_filename="menu.pdf",
        declared_media_type="application/pdf",
        content=b"%PDF-1.7\nfixture",
    )

    execution = await extraction.process_next(
        tenant_id="org_1",
        supplier_id="sup_1",
        document_id=uploaded.document.document_id,
    )

    assert execution.run.status is ExtractionRunStatus.COMPLETED
    assert execution.document_status is DocumentProcessingStatus.AWAITING_SUPPLIER_REVIEW
    fields = {field.field_name: field for field in execution.run.fields}
    assert fields["minimum_people"].status is ExtractionFieldStatus.NEEDS_REVIEW
    assert fields["invoice_available"].status is ExtractionFieldStatus.NOT_FOUND
    assert (
        await runs.get_for_document(
            tenant_id="org_1",
            supplier_id="sup_1",
            document_id="doc_1",
        )
    ) == execution.run
    assert [event.event_type for event in await events.all()] == [
        "SUPPLIER_EXTRACTION_STARTED",
        "SUPPLIER_EXTRACTION_COMPLETED",
    ]


@pytest.mark.asyncio
async def test_provider_failure_marks_failed_and_preserves_original() -> None:
    ingestion, extraction, _, events = collaborators(provider_fixtures={})
    original = b"\x89PNG\r\n\x1a\nfixture"
    uploaded = await ingestion.ingest_file(
        tenant_id="org_1",
        supplier_id="sup_1",
        original_filename="menu.png",
        declared_media_type="image/png",
        content=original,
    )

    execution = await extraction.process_next(
        tenant_id="org_1",
        supplier_id="sup_1",
        document_id=uploaded.document.document_id,
    )
    document, stored = await ingestion.get_material(
        tenant_id="org_1",
        supplier_id="sup_1",
        document_id=uploaded.document.document_id,
    )

    assert execution.run.status is ExtractionRunStatus.FAILED
    assert execution.run.failure_code == "PROVIDER_ERROR"
    assert document.status is DocumentProcessingStatus.EXTRACTION_FAILED
    assert stored == original
    failed_event = (await events.all())[-1]
    assert failed_event.event_type == "SUPPLIER_EXTRACTION_FAILED"
    assert set(failed_event.model_dump()) == {
        "event_type",
        "tenant_id",
        "supplier_id",
        "document_id",
        "job_id",
        "extraction_run_id",
        "occurred_at",
        "failure_code",
    }


@pytest.mark.asyncio
async def test_extraction_endpoint_processes_queue_and_exposes_tenant_bound_status() -> None:
    ingestion, extraction, _, _ = collaborators(provider_fixtures={"doc_1": {}})
    uploaded = await ingestion.ingest_text(
        tenant_id="org_1",
        supplier_id="sup_1",
        text="Atendemos eventos corporativos",
    )

    async def tenant_from_test_auth(request: Request) -> str:
        return request.headers.get("x-test-tenant", "org_1")

    app = FastAPI()
    app.include_router(
        create_extraction_router(
            service=extraction,
            resolve_tenant=tenant_from_test_auth,
        )
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        processed = await client.post(
            "/api/v1/suppliers/sup_1/extractions",
            json={"document_id": uploaded.document.document_id},
        )
        status = await client.get(
            f"/api/v1/suppliers/sup_1/extractions/{uploaded.document.document_id}"
        )
        wrong_tenant = await client.get(
            f"/api/v1/suppliers/sup_1/extractions/{uploaded.document.document_id}",
            headers={"x-test-tenant": "org_2"},
        )

    assert processed.status_code == 200
    assert processed.json()["document_status"] == "AWAITING_SUPPLIER_REVIEW"
    assert status.status_code == 200
    assert status.json()["run"]["status"] == "COMPLETED"
    assert wrong_tenant.status_code == 404
