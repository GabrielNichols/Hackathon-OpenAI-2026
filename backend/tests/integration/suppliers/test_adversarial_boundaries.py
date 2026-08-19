from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta

import pytest
from pydantic import ValidationError

from app.modules.suppliers.application.core_compat import (
    FakeAuditPort,
    FakeSupplierActivationCommandPort,
    FixedClock,
    ReviewTokenError,
    SignedReviewTokenService,
    SupplierLifecycleStatus,
)
from app.modules.suppliers.application.extraction_service import (
    ExtractionQueueEmptyError,
    ExtractionResultNotFoundError,
    SupplierExtractionService,
)
from app.modules.suppliers.application.ingestion import (
    MaterialNotFoundError,
    SupplierMaterialIngestionService,
    validate_material,
)
from app.modules.suppliers.application.review import (
    InMemorySupplierReviewRepository,
    ReviewConflictError,
    SupplierReviewService,
    SupplierReviewSession,
)
from app.modules.suppliers.extraction.models import (
    ExtractedFieldDTO,
    ExtractionFieldStatus,
    ProviderExtractedFieldDTO,
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
from app.modules.suppliers.search.directory import InMemorySupplierDirectory
from app.modules.suppliers.search.models import (
    SupplierDirectoryRecord,
    SupplierSearchCriteria,
)

NOW = datetime(2026, 8, 19, 15, tzinfo=UTC)


class SequenceIds:
    def __init__(self) -> None:
        self._counts: dict[str, int] = {}

    def new(self, prefix: str) -> str:
        self._counts[prefix] = self._counts.get(prefix, 0) + 1
        return f"{prefix}_{self._counts[prefix]}"


def ingestion_collaborators() -> tuple[
    SupplierMaterialIngestionService,
    InMemorySourceDocumentRepository,
    InMemoryExtractionQueue,
]:
    documents = InMemorySourceDocumentRepository()
    queue = InMemoryExtractionQueue()
    ingestion = SupplierMaterialIngestionService(
        storage=InMemoryDocumentStorage(),
        documents=documents,
        extraction_queue=queue,
        clock=FixedClock(NOW),
        ids=SequenceIds(),
        max_size_bytes=10_000,
    )
    return ingestion, documents, queue


@pytest.mark.asyncio
async def test_cross_tenant_uploads_use_distinct_blobs_and_cannot_be_downloaded() -> None:
    storage = InMemoryDocumentStorage()
    documents = InMemorySourceDocumentRepository()
    ingestion = SupplierMaterialIngestionService(
        storage=storage,
        documents=documents,
        extraction_queue=InMemoryExtractionQueue(),
        clock=FixedClock(NOW),
        ids=SequenceIds(),
        max_size_bytes=10_000,
    )
    content = b"%PDF-1.7\nshared bytes"
    first = await ingestion.ingest_file(
        tenant_id="org_1",
        supplier_id="sup_1",
        original_filename="first.pdf",
        declared_media_type="application/pdf",
        content=content,
    )
    second = await ingestion.ingest_file(
        tenant_id="org_2",
        supplier_id="sup_2",
        original_filename="second.pdf",
        declared_media_type="application/pdf",
        content=content,
    )

    assert first.document.document_id != second.document.document_id
    assert first.document.blob_id != second.document.blob_id
    with pytest.raises(MaterialNotFoundError):
        await ingestion.get_material(
            tenant_id="org_2",
            supplier_id="sup_1",
            document_id=first.document.document_id,
        )
    with pytest.raises(MaterialNotFoundError):
        await ingestion.get_material(
            tenant_id="org_1",
            supplier_id="sup_2",
            document_id=second.document.document_id,
        )


@pytest.mark.asyncio
async def test_wrong_tenant_and_supplier_cannot_claim_job_or_read_extraction_status() -> None:
    ingestion, documents, queue = ingestion_collaborators()
    uploaded = await ingestion.ingest_file(
        tenant_id="org_1",
        supplier_id="sup_1",
        original_filename="private-catalog.pdf",
        declared_media_type="application/pdf",
        content=b"%PDF-1.7\nprivate catalog",
    )
    events = InMemoryExtractionEventSink()
    extraction = SupplierExtractionService(
        ingestion=ingestion,
        documents=documents,
        queue=queue,
        provider=FakeSupplierExtractionProvider(
            fixtures={
                uploaded.document.document_id: {
                    "trade_name": {
                        "value": "Private Supplier",
                        "normalized_value": "Private Supplier",
                        "confidence": 0.99,
                        "source_excerpt": "CONFIDENTIAL COMMERCIAL CONTENT",
                    }
                }
            },
            fixed_now=NOW,
        ),
        runs=InMemoryExtractionRunRepository(),
        events=events,
        clock=FixedClock(NOW),
    )

    with pytest.raises(ExtractionQueueEmptyError):
        await extraction.process_next(
            tenant_id="org_2",
            supplier_id="sup_1",
            document_id=uploaded.document.document_id,
        )
    with pytest.raises(ExtractionQueueEmptyError):
        await extraction.process_next(
            tenant_id="org_1",
            supplier_id="sup_other",
            document_id=uploaded.document.document_id,
        )

    completed = await extraction.process_next(
        tenant_id="org_1",
        supplier_id="sup_1",
        document_id=uploaded.document.document_id,
    )
    assert completed.run.status == "COMPLETED"
    with pytest.raises(ExtractionResultNotFoundError):
        await extraction.get_status(
            tenant_id="org_2",
            supplier_id="sup_1",
            document_id=uploaded.document.document_id,
        )
    with pytest.raises(ExtractionResultNotFoundError):
        await extraction.get_status(
            tenant_id="org_1",
            supplier_id="sup_other",
            document_id=uploaded.document.document_id,
        )

    serialized_events = json.dumps([event.model_dump(mode="json") for event in await events.all()])
    assert "CONFIDENTIAL COMMERCIAL CONTENT" not in serialized_events
    assert "private-catalog.pdf" not in serialized_events
    assert "private catalog" not in serialized_events


@pytest.mark.asyncio
async def test_consumed_review_token_cannot_modify_fields_after_submit() -> None:
    extracted = ExtractedFieldDTO(
        field_name="trade_name",
        value="Alpha",
        normalized_value="Alpha",
        status=ExtractionFieldStatus.EXTRACTED,
        confidence=0.9,
        source_document_id="doc_1",
        source_page=1,
        source_excerpt="Alpha",
        extraction_run_id="ext_1",
        version=1,
    )
    repository = InMemorySupplierReviewRepository()
    repository.add(
        SupplierReviewSession.from_extracted_fields(
            review_id="review_1",
            tenant_id="org_1",
            supplier_id="sup_1",
            recipient_id="contact_1",
            required_fields=("trade_name",),
            fields=[extracted],
        )
    )
    clock = FixedClock(NOW)
    tokens = SignedReviewTokenService(secret=b"adversarial-secret", clock=clock)
    token = tokens.issue(
        tenant_id="org_1",
        supplier_id="sup_1",
        recipient_id="contact_1",
        expires_at=NOW + timedelta(hours=1),
        nonce="single-use-submit",
    )
    activation = FakeSupplierActivationCommandPort(clock=clock)
    service = SupplierReviewService(
        repository=repository,
        token_service=tokens,
        activation_port=activation,
        audit_port=FakeAuditPort(),
        clock=clock,
    )
    await service.confirm_field(token, "trade_name", expected_version=1)
    await service.submit(token)

    with pytest.raises((ReviewTokenError, ReviewConflictError)) as error:
        await service.correct_field(
            token,
            "trade_name",
            value="Mutated after submit",
            normalized_value="Mutated after submit",
            expected_version=2,
        )

    assert error.value.code in {"LINK_INVALID", "CONFLICT"}
    stored = repository.get("review_1")
    assert stored.current_field("trade_name").value == "Alpha"
    assert len(stored.field_history("trade_name")) == 2
    assert len(activation.commands) == 1


def test_provider_output_cannot_forge_confirmation_even_with_human_like_metadata() -> None:
    with pytest.raises(ValidationError):
        ProviderExtractedFieldDTO.model_validate(
            {
                "field_name": "invoice_available",
                "value": True,
                "normalized_value": True,
                "status": "confirmed",
                "confidence": 1,
                "confirmed_by": "model-pretending-to-be-human",
                "confirmed_at": NOW,
            }
        )


def test_windows_path_and_header_injection_are_removed_from_sanitized_filename() -> None:
    material = validate_material(
        original_filename="C:\\private\\..\\menu\r\nX-Leak: yes.PDF",
        declared_media_type="application/pdf",
        content=b"%PDF-1.7\nfixture",
        max_size_bytes=1_000,
    )

    assert material.original_filename.endswith("yes.PDF")
    assert material.sanitized_filename == "menuX-Leak_yes.pdf"
    assert not {"/", "\\", "\r", "\n", ":"} & set(material.sanitized_filename)


@pytest.mark.asyncio
async def test_unknown_invoice_status_is_never_promoted_to_true_or_invoice_eligible() -> None:
    record = SupplierDirectoryRecord(
        tenant_id="org_1",
        supplier_id="sup_1",
        display_name="Unknown Invoice Supplier",
        status=SupplierLifecycleStatus.ACTIVE,
        profile_confirmed=True,
        categories=["corporate_catering"],
        service_cities=["São Paulo"],
        service_districts=[],
        minimum_people=10,
        maximum_people=100,
        lead_time_hours=24,
        invoice_available=None,
        dietary_capabilities={"vegetarian": "unknown"},
        sustainability_tags=[],
        last_confirmed_at=NOW,
        evidence_refs=["evidence://sup_1/profile/v1"],
    )
    directory = InMemorySupplierDirectory(tenant_id="org_1", records=[record])
    base = {
        "tenant_id": "org_1",
        "category": "corporate_catering",
        "city": "São Paulo",
        "district": None,
        "event_date": date(2026, 8, 28),
        "delivery_time": None,
        "people_count": 50,
        "dietary_requirements": {},
        "mandatory_tags": [],
        "maximum_lead_time_hours": None,
    }
    broad = await directory.search(
        SupplierSearchCriteria.model_validate({**base, "invoice_required": False})
    )
    requiring_invoice = await directory.search(
        SupplierSearchCriteria.model_validate({**base, "invoice_required": True})
    )

    assert broad[0].invoice_available is None
    assert "invoice_available" in broad[0].missing_fields
    assert requiring_invoice == []
