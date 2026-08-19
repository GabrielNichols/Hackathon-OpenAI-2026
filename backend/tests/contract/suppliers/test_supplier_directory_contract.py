from datetime import UTC, date, datetime

import pytest

from app.modules.suppliers.application.core_compat import SupplierLifecycleStatus
from app.modules.suppliers.search.directory import InMemorySupplierDirectory
from app.modules.suppliers.search.models import (
    SupplierCandidateDTO,
    SupplierDirectoryRecord,
    SupplierSearchCriteria,
)

CONFIRMED_AT = datetime(2026, 8, 19, 15, 0, tzinfo=UTC)


def criteria(**overrides: object) -> SupplierSearchCriteria:
    values: dict[str, object] = {
        "tenant_id": "org_demo",
        "category": "corporate_catering",
        "city": "São Paulo",
        "district": "Vila Olímpia",
        "event_date": date(2026, 8, 28),
        "delivery_time": None,
        "people_count": 80,
        "invoice_required": False,
        "dietary_requirements": {"vegetarian": 12},
        "mandatory_tags": [],
        "maximum_lead_time_hours": 72,
    }
    values.update(overrides)
    return SupplierSearchCriteria.model_validate(values)


def record(
    supplier_id: str,
    *,
    tenant_id: str = "org_demo",
    status: SupplierLifecycleStatus = SupplierLifecycleStatus.ACTIVE,
    profile_confirmed: bool = True,
    invoice_available: bool | None = True,
    last_confirmed_at: datetime | None = CONFIRMED_AT,
) -> SupplierDirectoryRecord:
    return SupplierDirectoryRecord(
        tenant_id=tenant_id,
        supplier_id=supplier_id,
        display_name=f"Supplier {supplier_id}",
        status=status,
        profile_confirmed=profile_confirmed,
        categories=["corporate_catering"],
        service_cities=["São Paulo"],
        service_districts=["Vila Olímpia"],
        minimum_people=20,
        maximum_people=120,
        lead_time_hours=48,
        invoice_available=invoice_available,
        dietary_capabilities={
            "vegetarian": "supported",
            "vegan": "unknown",
            "gluten_free": "supported_with_cross_contamination_warning",
        },
        sustainability_tags=["no_single_use_plastic"],
        last_confirmed_at=last_confirmed_at,
        evidence_refs=[f"evidence://{supplier_id}/invoice_available/v2"],
    )


@pytest.mark.asyncio
async def test_supplier_directory_returns_only_active_confirmed_suppliers() -> None:
    directory = InMemorySupplierDirectory(
        tenant_id="org_demo",
        records=[
            record("sup_active"),
            record("sup_draft", status=SupplierLifecycleStatus.DRAFT),
            record("sup_unconfirmed", profile_confirmed=False),
            record("sup_without_confirmation_time", last_confirmed_at=None),
            record("sup_other_tenant", tenant_id="org_other"),
        ],
    )

    candidates = await directory.search(criteria())

    assert [candidate.supplier_id for candidate in candidates] == ["sup_active"]
    assert candidates[0].status == SupplierLifecycleStatus.ACTIVE


@pytest.mark.asyncio
async def test_supplier_directory_never_marks_unknown_invoice_status_as_true() -> None:
    directory = InMemorySupplierDirectory(
        tenant_id="org_demo",
        records=[record("sup_unknown_invoice", invoice_available=None)],
    )

    broad_result = await directory.search(criteria(invoice_required=False))
    required_result = await directory.search(criteria(invoice_required=True))

    assert broad_result[0].invoice_available is None
    assert "invoice_available" in broad_result[0].missing_fields
    assert required_result == []


@pytest.mark.asyncio
async def test_supplier_search_result_contains_stable_evidence_refs() -> None:
    directory = InMemorySupplierDirectory(
        tenant_id="org_demo",
        records=[record("sup_evidence")],
    )

    [candidate] = await directory.search(criteria())

    assert candidate.evidence_refs == ["evidence://sup_evidence/invoice_available/v2"]
    assert SupplierCandidateDTO.model_validate(candidate.model_dump()) == candidate


@pytest.mark.asyncio
async def test_supplier_directory_get_is_tenant_scoped() -> None:
    directory = InMemorySupplierDirectory(
        tenant_id="org_demo",
        records=[record("sup_demo"), record("sup_other", tenant_id="org_other")],
    )

    assert (await directory.get("sup_demo")) is not None
    assert await directory.get("sup_other") is None


def test_supplier_search_criteria_and_candidate_are_json_serializable_contracts() -> None:
    serialized_criteria = criteria().model_dump(mode="json")
    candidate = SupplierCandidateDTO(
        supplier_id="sup_alpha",
        display_name="Alpha",
        status=SupplierLifecycleStatus.ACTIVE,
        categories=["corporate_catering"],
        service_areas=["São Paulo/Vila Olímpia"],
        minimum_people=20,
        maximum_people=120,
        lead_time_hours=48,
        invoice_available=True,
        dietary_capabilities={"vegetarian": "supported"},
        sustainability_tags=[],
        last_confirmed_at=CONFIRMED_AT,
        evidence_refs=["evidence://sup_alpha/trade_name/v2"],
        missing_fields=[],
    )

    assert serialized_criteria["event_date"] == "2026-08-28"
    assert candidate.model_dump(mode="json")["last_confirmed_at"] == "2026-08-19T15:00:00Z"
