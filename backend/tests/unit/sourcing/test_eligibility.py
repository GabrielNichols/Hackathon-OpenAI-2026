from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta

import pytest
from app.modules.sourcing import (
    EligibilityCheck,
    EligibilityDecision,
    EligibilityOutcome,
    SupplierCandidateDTO,
    SupplierEligibilityEngine,
    SupplierEligibilityResult,
    SupplierSearchCriteria,
)
from pydantic import ValidationError

NOW = datetime(2026, 8, 19, 12, tzinfo=UTC)


def criteria(**changes: object) -> SupplierSearchCriteria:
    values: dict[str, object] = {
        "tenant_id": "tenant-1",
        "category": "corporate_catering",
        "city": "São Paulo",
        "district": "Pinheiros",
        "event_date": date(2026, 9, 1),
        "delivery_time": time(12),
        "people_count": 80,
        "invoice_required": True,
        "dietary_requirements": {"vegetarian": 4, "vegan": 2},
        "mandatory_tags": ["no_single_use_plastic"],
        "maximum_lead_time_hours": 72,
    }
    values.update(changes)
    return SupplierSearchCriteria.model_validate(values)


def candidate(**changes: object) -> SupplierCandidateDTO:
    values: dict[str, object] = {
        "supplier_id": "supplier-1",
        "display_name": "Buffet Um",
        "status": "ACTIVE",
        "categories": ["corporate_catering"],
        "service_areas": ["São Paulo / Pinheiros"],
        "minimum_people": 20,
        "maximum_people": 200,
        "lead_time_hours": 48,
        "invoice_available": True,
        "dietary_capabilities": {"vegetarian": "supported", "vegan": "confirmed"},
        "sustainability_tags": ["no_single_use_plastic"],
        "last_confirmed_at": NOW - timedelta(days=20),
        "evidence_refs": ["supplier-profile:v4", "document:menu:p2"],
        "missing_fields": [],
    }
    values.update(changes)
    return SupplierCandidateDTO.model_validate(values)


def check_by_criterion(result: SupplierEligibilityResult, criterion: str) -> EligibilityCheck:
    return next(check for check in result.checks if check.criterion == criterion)


def test_all_confirmed_criteria_are_eligible_and_explainable() -> None:
    result = SupplierEligibilityEngine().evaluate(candidate(), criteria(), as_of=NOW)

    assert result.decision is EligibilityDecision.ELIGIBLE
    assert all(check.outcome is EligibilityOutcome.PASS for check in result.checks)
    assert all(check.passed is True for check in result.checks)
    assert all(check.reason_code for check in result.checks)
    assert all(check.evidence == result.evidence_refs for check in result.checks)
    assert result.model_dump(mode="json")["decision"] == "eligible"


def test_none_invoice_is_unknown_and_never_treated_as_true() -> None:
    result = SupplierEligibilityEngine().evaluate(
        candidate(invoice_available=None, missing_fields=["invoice_available"]),
        criteria(),
        as_of=NOW,
    )

    invoice = check_by_criterion(result, "invoice_available")
    assert result.decision is EligibilityDecision.NEEDS_REFRESH
    assert invoice.outcome is EligibilityOutcome.UNKNOWN
    assert invoice.passed is None
    assert invoice.reason_code == "INVOICE_STATUS_UNKNOWN"


def test_supplier_without_invoice_is_excluded_when_invoice_is_required() -> None:
    result = SupplierEligibilityEngine().evaluate(
        candidate(invoice_available=False), criteria(), as_of=NOW
    )

    assert result.decision is EligibilityDecision.EXCLUDED
    assert check_by_criterion(result, "invoice_available").reason_code == "INVOICE_UNAVAILABLE"


def test_supplier_outside_service_area_is_excluded() -> None:
    result = SupplierEligibilityEngine().evaluate(
        candidate(service_areas=["Campinas"]), criteria(), as_of=NOW
    )

    assert result.decision is EligibilityDecision.EXCLUDED
    assert check_by_criterion(result, "service_area").reason_code == "SERVICE_AREA_MISMATCH"


@pytest.mark.parametrize(
    ("change", "reason_code"),
    [
        ({"minimum_people": 81}, "BELOW_MINIMUM_PEOPLE"),
        ({"maximum_people": 79}, "CAPACITY_EXCEEDED"),
    ],
)
def test_supplier_outside_either_capacity_boundary_is_excluded(
    change: dict[str, int], reason_code: str
) -> None:
    result = SupplierEligibilityEngine().evaluate(candidate(**change), criteria(), as_of=NOW)

    assert result.decision is EligibilityDecision.EXCLUDED
    assert reason_code in {check.reason_code for check in result.checks}


def test_incompatible_category_and_lead_time_are_explicit_failures() -> None:
    result = SupplierEligibilityEngine().evaluate(
        candidate(categories=["office_supplies"], lead_time_hours=96),
        criteria(),
        as_of=NOW,
    )

    assert result.decision is EligibilityDecision.EXCLUDED
    assert {"CATEGORY_MISMATCH", "LEAD_TIME_EXCEEDED"} <= {
        check.reason_code for check in result.checks
    }


def test_each_required_diet_has_a_stable_check_and_unknown_blocks_eligibility() -> None:
    result = SupplierEligibilityEngine().evaluate(
        candidate(dietary_capabilities={"vegetarian": "yes"}),
        criteria(),
        as_of=NOW,
    )

    dietary = [check for check in result.checks if check.criterion.startswith("dietary:")]
    assert [check.criterion for check in dietary] == ["dietary:vegan", "dietary:vegetarian"]
    assert dietary[0].reason_code == "DIETARY_CAPABILITY_UNKNOWN"
    assert dietary[1].reason_code == "DIETARY_REQUIREMENT_MET"
    assert result.decision is EligibilityDecision.NEEDS_REFRESH


def test_explicitly_unsupported_diet_takes_precedence_over_an_unknown_field() -> None:
    result = SupplierEligibilityEngine().evaluate(
        candidate(
            dietary_capabilities={"vegetarian": "supported", "vegan": "unsupported"},
            minimum_people=None,
            missing_fields=["minimum_people"],
        ),
        criteria(),
        as_of=NOW,
    )

    assert any(check.outcome is EligibilityOutcome.FAIL for check in result.checks)
    assert any(check.outcome is EligibilityOutcome.UNKNOWN for check in result.checks)
    assert result.decision is EligibilityDecision.EXCLUDED


def test_stale_missing_and_future_confirmation_require_refresh() -> None:
    engine = SupplierEligibilityEngine(max_profile_age=timedelta(days=30))

    stale = engine.evaluate(
        candidate(last_confirmed_at=NOW - timedelta(days=30, seconds=1)),
        criteria(),
        as_of=NOW,
    )
    missing = engine.evaluate(candidate(last_confirmed_at=None), criteria(), as_of=NOW)
    future = engine.evaluate(
        candidate(last_confirmed_at=NOW + timedelta(seconds=1)), criteria(), as_of=NOW
    )

    assert stale.decision is EligibilityDecision.NEEDS_REFRESH
    assert check_by_criterion(stale, "freshness").reason_code == "PROFILE_STALE"
    assert check_by_criterion(missing, "freshness").reason_code == "LAST_CONFIRMATION_MISSING"
    assert check_by_criterion(future, "freshness").reason_code == "LAST_CONFIRMATION_IN_FUTURE"


def test_freshness_boundary_is_inclusive_and_handles_naive_directory_timestamp() -> None:
    engine = SupplierEligibilityEngine(max_profile_age=timedelta(days=30))
    naive_boundary = (NOW - timedelta(days=30)).replace(tzinfo=None)

    result = engine.evaluate(candidate(last_confirmed_at=naive_boundary), criteria(), as_of=NOW)

    assert result.decision is EligibilityDecision.ELIGIBLE
    assert check_by_criterion(result, "freshness").reason_code == "PROFILE_FRESH"


def test_only_critical_missing_fields_block_eligibility() -> None:
    irrelevant = SupplierEligibilityEngine().evaluate(
        candidate(missing_fields=["contact_phone"]), criteria(), as_of=NOW
    )
    critical = SupplierEligibilityEngine().evaluate(
        candidate(missing_fields=["maximum_people"]), criteria(), as_of=NOW
    )

    assert irrelevant.decision is EligibilityDecision.ELIGIBLE
    assert critical.decision is EligibilityDecision.NEEDS_REFRESH
    assert check_by_criterion(critical, "missing_fields").reason_code == "CRITICAL_FIELD_MISSING"


def test_search_contract_rejects_extra_fields_and_invalid_counts() -> None:
    payload = criteria().model_dump()
    payload["hallucinated_field"] = "value"

    with pytest.raises(ValidationError):
        SupplierSearchCriteria.model_validate(payload)
    with pytest.raises(ValidationError):
        criteria(people_count=0)
    with pytest.raises(ValidationError):
        criteria(dietary_requirements={"vegan": -1})
