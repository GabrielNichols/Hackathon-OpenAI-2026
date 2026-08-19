from datetime import UTC, datetime
from typing import Annotated

import pytest
from fastapi import FastAPI, Header
from httpx import ASGITransport, AsyncClient

from app.modules.suppliers.api.profile_router import create_supplier_profile_router
from app.modules.suppliers.application.core_compat import FakeAuditPort, FixedClock
from app.modules.suppliers.application.review_profile import (
    InMemorySupplierProfileRepository,
    SupplierProfileService,
)

NOW = datetime(2026, 8, 19, 15, 0, tzinfo=UTC)


class SequentialIds:
    def __init__(self) -> None:
        self._sequence = 0

    def new(self, prefix: str) -> str:
        self._sequence += 1
        return f"{prefix}_{self._sequence}"


def resolve_tenant(x_tenant_id: Annotated[str, Header(alias="X-Tenant-ID")]) -> str:
    return x_tenant_id


def profile_app() -> tuple[FastAPI, FakeAuditPort]:
    audit = FakeAuditPort()
    service = SupplierProfileService(
        repository=InMemorySupplierProfileRepository(),
        audit_port=audit,
        clock=FixedClock(NOW),
        id_generator=SequentialIds(),
    )
    app = FastAPI()
    app.include_router(
        create_supplier_profile_router(service=service, resolve_tenant=resolve_tenant)
    )
    return app, audit


@pytest.mark.asyncio
async def test_create_and_get_supplier_profile_are_exportable_and_tenant_scoped() -> None:
    app, audit = profile_app()
    payload = {
        "legal_name": "Alpha Alimentos Ltda",
        "trade_name": "Alpha Catering",
        "cnpj": "00000000000100",
        "contact_name": "Supplier Contact",
        "contact_email": "supplier@example.test",
        "contact_phone": "+5511999999999",
    }
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post(
            "/api/v1/suppliers",
            headers={"X-Tenant-ID": "org_demo"},
            json=payload,
        )
        fetched = await client.get(
            "/api/v1/suppliers/sup_1",
            headers={"X-Tenant-ID": "org_demo"},
        )
        cross_tenant = await client.get(
            "/api/v1/suppliers/sup_1",
            headers={"X-Tenant-ID": "org_other"},
        )

    assert created.status_code == 201
    assert created.json() == {
        "supplier_id": "sup_1",
        "organization_id": "org_demo",
        "legal_name": "Alpha Alimentos Ltda",
        "trade_name": "Alpha Catering",
        "cnpj": "00000000000100",
        "contact_id": "contact_2",
        "contact_name": "Supplier Contact",
        "contact_email": "supplier@example.test",
        "contact_phone": "+5511999999999",
        "status": "DRAFT",
        "last_confirmed_at": None,
        "created_at": "2026-08-19T15:00:00Z",
        "version": 1,
    }
    assert fetched.status_code == 200
    assert fetched.json() == created.json()
    assert cross_tenant.status_code == 404
    assert cross_tenant.json()["error"]["code"] == "NOT_FOUND"
    assert [event.event_type for event in audit.events] == ["SUPPLIER_CREATED"]
    assert audit.events[0].payload == {"contact_id": "contact_2"}


@pytest.mark.asyncio
async def test_create_supplier_does_not_accept_caller_controlled_state_or_tenant() -> None:
    app, _ = profile_app()
    payload = {
        "legal_name": "Alpha Alimentos Ltda",
        "trade_name": "Alpha Catering",
        "contact_name": "Supplier Contact",
        "contact_email": "supplier@example.test",
        "contact_phone": "+5511999999999",
        "status": "ACTIVE",
        "organization_id": "org_other",
    }

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/v1/suppliers",
            headers={"X-Tenant-ID": "org_demo"},
            json=payload,
        )

    assert response.status_code == 422
