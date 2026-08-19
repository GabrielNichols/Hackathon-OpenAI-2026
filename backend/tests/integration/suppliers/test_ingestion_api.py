from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest
from fastapi import FastAPI, Request

from app.modules.suppliers.api.ingestion_router import create_ingestion_router
from app.modules.suppliers.application.ingestion import SupplierMaterialIngestionService
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
        self.value = 0

    def new(self, prefix: str) -> str:
        self.value += 1
        return f"{prefix}_{self.value}"


@pytest.fixture
def app() -> FastAPI:
    service = SupplierMaterialIngestionService(
        storage=InMemoryDocumentStorage(),
        documents=InMemorySourceDocumentRepository(),
        extraction_queue=FakeExtractionQueue(),
        clock=FixedClock(),
        ids=SequenceIds(),
        max_size_bytes=1_000,
    )

    async def tenant_from_test_auth(request: Request) -> str:
        return request.headers.get("x-test-tenant", "org_1")

    test_app = FastAPI()
    test_app.include_router(
        create_ingestion_router(service=service, resolve_tenant=tenant_from_test_auth)
    )
    return test_app


@pytest.mark.asyncio
async def test_upload_and_download_original_material(app: FastAPI) -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/v1/suppliers/sup_1/materials",
            files={"file": ("menu.pdf", b"%PDF-1.7\nfixture", "application/pdf")},
        )

        assert response.status_code == 201
        payload = response.json()
        assert payload["status"] == "EXTRACTION_QUEUED"
        assert payload["sanitized_filename"] == "menu.pdf"
        download = await client.get(
            f"/api/v1/suppliers/sup_1/materials/{payload['document_id']}"
        )

    assert download.status_code == 200
    assert download.content == b"%PDF-1.7\nfixture"
    assert download.headers["x-document-status"] == "EXTRACTION_QUEUED"
    assert download.headers["content-disposition"] == 'attachment; filename="menu.pdf"'


@pytest.mark.asyncio
async def test_text_material_can_be_submitted_as_json(app: FastAPI) -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/v1/suppliers/sup_1/materials",
            json={"text": "Mensagem copiada do WhatsApp", "filename": "whatsapp.txt"},
        )

    assert response.status_code == 201
    assert response.json()["media_type"] == "text/plain"


@pytest.mark.asyncio
async def test_empty_upload_returns_stable_error(app: FastAPI) -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/v1/suppliers/sup_1/materials",
            files={"file": ("empty.pdf", b"", "application/pdf")},
        )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "EMPTY_MATERIAL"


@pytest.mark.asyncio
async def test_download_is_bound_to_supplier_and_tenant(app: FastAPI) -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        created = await client.post(
            "/api/v1/suppliers/sup_1/materials",
            files={"file": ("menu.pdf", b"%PDF-1.7\nfixture", "application/pdf")},
            headers={"x-test-tenant": "org_1"},
        )
        document_id = created.json()["document_id"]

        wrong_supplier = await client.get(
            f"/api/v1/suppliers/sup_2/materials/{document_id}",
            headers={"x-test-tenant": "org_1"},
        )
        wrong_tenant = await client.get(
            f"/api/v1/suppliers/sup_1/materials/{document_id}",
            headers={"x-test-tenant": "org_2"},
        )

    assert wrong_supplier.status_code == 404
    assert wrong_tenant.status_code == 404
