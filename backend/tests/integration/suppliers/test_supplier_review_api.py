from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.modules.suppliers.api.review_router import router
from app.modules.suppliers.application.core_compat import (
    FakeAuditPort,
    FakeSupplierActivationCommandPort,
    FixedClock,
    SignedReviewTokenService,
)
from app.modules.suppliers.application.review import (
    InMemorySupplierReviewRepository,
    SupplierReviewService,
    SupplierReviewSession,
)
from app.modules.suppliers.extraction.models import ExtractedFieldDTO, ExtractionFieldStatus


NOW = datetime(2026, 8, 19, 15, 0, tzinfo=UTC)


def extracted(field_name: str, value: object) -> ExtractedFieldDTO:
    return ExtractedFieldDTO(
        field_name=field_name,
        value=value,
        normalized_value=value,
        status=ExtractionFieldStatus.EXTRACTED,
        confidence=0.9,
        source_document_id="doc_1",
        source_page=1,
        source_excerpt=f"evidence for {field_name}",
        extraction_run_id="run_1",
        version=1,
    )


def api_context(
    *,
    required_fields: tuple[str, ...] = ("trade_name", "invoice_available"),
    expires_at: datetime | None = None,
    token_supplier_id: str = "sup_alpha",
) -> tuple[FastAPI, str, FakeSupplierActivationCommandPort, FakeAuditPort]:
    repository = InMemorySupplierReviewRepository()
    repository.add(
        SupplierReviewSession.from_extracted_fields(
            review_id="review_1",
            tenant_id="org_demo",
            supplier_id="sup_alpha",
            recipient_id="contact_alpha",
            required_fields=required_fields,
            fields=[extracted("trade_name", "Alpha"), extracted("invoice_available", True)],
        )
    )
    clock = FixedClock(NOW)
    token_service = SignedReviewTokenService(secret=b"api-test-secret", clock=clock)
    token = token_service.issue(
        tenant_id="org_demo",
        supplier_id=token_supplier_id,
        recipient_id="contact_alpha",
        expires_at=expires_at or NOW + timedelta(hours=1),
        nonce="nonce_api",
    )
    activation = FakeSupplierActivationCommandPort(clock=clock)
    audit = FakeAuditPort()
    service = SupplierReviewService(
        repository=repository,
        token_service=token_service,
        activation_port=activation,
        audit_port=audit,
        clock=clock,
    )
    app = FastAPI()
    app.state.supplier_review_service = service
    app.include_router(router)
    return app, token, activation, audit


@pytest.mark.asyncio
async def test_review_api_confirms_fields_submits_and_activates_through_command_port() -> None:
    app, token, activation, audit = api_context()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        initial = await client.get(f"/api/v1/supplier-review/{token}")
        trade_name = await client.post(
            f"/api/v1/supplier-review/{token}/fields/trade_name/confirm",
            json={"expected_version": 1},
        )
        invoice = await client.post(
            f"/api/v1/supplier-review/{token}/fields/invoice_available/confirm",
            json={"expected_version": 1},
        )
        submitted = await client.post(f"/api/v1/supplier-review/{token}/submit")
        replayed = await client.post(f"/api/v1/supplier-review/{token}/submit")

    assert initial.status_code == 200
    assert initial.json()["missing_required_fields"] == ["trade_name", "invoice_available"]
    assert trade_name.status_code == 200
    assert trade_name.json()["decision"] == "confirmed"
    assert invoice.status_code == 200
    assert submitted.status_code == 200
    assert submitted.json()["status"] == "ACTIVE"
    assert replayed.status_code == 403
    assert replayed.json()["error"]["code"] == "LINK_INVALID"
    assert len(activation.commands) == 1
    assert audit.events[-1].event_type == "SUPPLIER_ACTIVATED"


@pytest.mark.asyncio
async def test_review_api_correction_is_versioned_and_returns_source_evidence() -> None:
    app, token, _, _ = api_context(required_fields=("trade_name",))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        corrected = await client.post(
            f"/api/v1/supplier-review/{token}/fields/trade_name/correct",
            json={
                "expected_version": 1,
                "value": "Alpha Catering",
                "normalized_value": "Alpha Catering",
            },
        )
        review = await client.get(f"/api/v1/supplier-review/{token}")

    assert corrected.status_code == 200
    assert corrected.json()["version"] == 2
    assert corrected.json()["decision"] == "corrected"
    current = next(field for field in review.json()["fields"] if field["field_name"] == "trade_name")
    assert current["source_document_id"] == "doc_1"
    assert current["source_excerpt"] == "evidence for trade_name"


@pytest.mark.asyncio
async def test_review_api_blocks_incomplete_submit_and_allows_same_token_to_finish() -> None:
    app, token, activation, _ = api_context()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post(
            f"/api/v1/supplier-review/{token}/fields/trade_name/confirm",
            json={"expected_version": 1},
        )
        blocked = await client.post(f"/api/v1/supplier-review/{token}/submit")
        await client.post(
            f"/api/v1/supplier-review/{token}/fields/invoice_available/confirm",
            json={"expected_version": 1},
        )
        completed = await client.post(f"/api/v1/supplier-review/{token}/submit")

    assert blocked.status_code == 409
    assert blocked.json()["error"] == {
        "code": "REVIEW_INCOMPLETE",
        "message": "supplier review has missing required fields",
        "details": {"missing_fields": ["invoice_available"]},
    }
    assert completed.status_code == 200
    assert len(activation.commands) == 1


@pytest.mark.asyncio
async def test_review_api_rejects_expired_and_wrong_supplier_tokens_without_leaking_review() -> None:
    expired_app, expired_token, _, _ = api_context(expires_at=NOW)
    wrong_app, wrong_token, _, _ = api_context(token_supplier_id="sup_other")

    async with AsyncClient(
        transport=ASGITransport(app=expired_app), base_url="http://test"
    ) as client:
        expired = await client.get(f"/api/v1/supplier-review/{expired_token}")
    async with AsyncClient(
        transport=ASGITransport(app=wrong_app), base_url="http://test"
    ) as client:
        wrong_supplier = await client.get(f"/api/v1/supplier-review/{wrong_token}")

    assert expired.status_code == 410
    assert expired.json()["error"]["code"] == "LINK_EXPIRED"
    assert wrong_supplier.status_code == 403
    assert wrong_supplier.json()["error"]["code"] == "LINK_INVALID"
