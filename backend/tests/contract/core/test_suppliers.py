from __future__ import annotations

from datetime import UTC, date, datetime, time

import pytest
from pydantic import ValidationError

from app.contracts import SupplierCandidateDTO, SupplierSearchCriteria, SupplierState


def test_supplier_search_criteria_round_trips_iso_dates_and_times() -> None:
    criteria = SupplierSearchCriteria(
        tenant_id="org_demo",
        category="corporate_catering",
        city="São Paulo",
        district="Vila Olímpia",
        event_date=date(2026, 8, 28),
        delivery_time=time(8, 30),
        people_count=80,
        invoice_required=True,
        dietary_requirements={"vegetarian": 12, "vegan": 4, "gluten_free": 3},
        mandatory_tags=["no_single_use_plastic"],
        maximum_lead_time_hours=216,
    )

    payload = criteria.model_dump(mode="json")
    assert payload["event_date"] == "2026-08-28"
    assert payload["delivery_time"] == "08:30:00"
    assert SupplierSearchCriteria.model_validate_json(criteria.model_dump_json()) == criteria


def test_supplier_search_counts_reject_float_and_negative_values() -> None:
    valid = {
        "tenant_id": "org_demo",
        "category": "corporate_catering",
        "city": "São Paulo",
        "district": None,
        "event_date": "2026-08-28",
        "delivery_time": None,
        "people_count": 80,
        "invoice_required": True,
        "dietary_requirements": {},
        "mandatory_tags": [],
        "maximum_lead_time_hours": None,
    }
    for invalid_count in (-1, 80.5, True):
        with pytest.raises(ValidationError):
            SupplierSearchCriteria.model_validate({**valid, "people_count": invalid_count})


def test_supplier_candidate_uses_state_and_evidence_refs() -> None:
    candidate = SupplierCandidateDTO(
        supplier_id="sup_alpha",
        display_name="Supplier Alpha",
        status=SupplierState.ACTIVE,
        categories=["corporate_catering"],
        service_areas=["São Paulo/Vila Olímpia"],
        minimum_people=30,
        maximum_people=200,
        lead_time_hours=48,
        invoice_available=True,
        dietary_capabilities={"vegan": "confirmed"},
        sustainability_tags=["no_single_use_plastic"],
        last_confirmed_at=datetime(2026, 8, 19, 15, 0, tzinfo=UTC),
        evidence_refs=["evi_supplier_alpha_menu"],
        missing_fields=[],
    )

    assert candidate.status is SupplierState.ACTIVE
    assert SupplierCandidateDTO.model_validate_json(candidate.model_dump_json()) == candidate


def test_supplier_candidate_rejects_noncanonical_id_prefix() -> None:
    with pytest.raises(ValidationError):
        SupplierCandidateDTO(
            supplier_id="supplier_alpha",
            display_name="Supplier Alpha",
            status="ACTIVE",
            categories=[],
            service_areas=[],
            dietary_capabilities={},
            sustainability_tags=[],
            evidence_refs=[],
            missing_fields=[],
        )
