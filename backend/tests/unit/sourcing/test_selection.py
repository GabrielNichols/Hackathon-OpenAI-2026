from __future__ import annotations

import pytest
from app.modules.sourcing import (
    DeterministicSupplierSelector,
    EligibilityCheck,
    EligibilityDecision,
    EligibilityOutcome,
    SupplierEligibilityResult,
    select_rfq_recipients,
)
from pydantic import ValidationError


def result(supplier_id: str, decision: EligibilityDecision) -> SupplierEligibilityResult:
    outcome = {
        EligibilityDecision.ELIGIBLE: EligibilityOutcome.PASS,
        EligibilityDecision.EXCLUDED: EligibilityOutcome.FAIL,
        EligibilityDecision.NEEDS_REFRESH: EligibilityOutcome.UNKNOWN,
    }[decision]
    return SupplierEligibilityResult(
        supplier_id=supplier_id,
        decision=decision,
        checks=[
            EligibilityCheck(
                criterion="fixture",
                required_value=True,
                actual_value=True,
                outcome=outcome,
                reason_code="FIXTURE",
                evidence=["fixture:evidence"],
            )
        ],
        evidence_refs=["fixture:evidence"],
    )


def test_selection_is_order_independent_limited_and_eligible_only() -> None:
    results = [
        result("supplier-c", EligibilityDecision.ELIGIBLE),
        result("supplier-refresh", EligibilityDecision.NEEDS_REFRESH),
        result("supplier-b", EligibilityDecision.ELIGIBLE),
        result("supplier-excluded", EligibilityDecision.EXCLUDED),
        result("supplier-a", EligibilityDecision.ELIGIBLE),
    ]

    expected = ["supplier-a", "supplier-b"]
    assert select_rfq_recipients(results, limit=2) == expected
    assert select_rfq_recipients(reversed(results), limit=2) == expected
    assert DeterministicSupplierSelector().select(results, limit=2) == expected


def test_duplicate_supplier_is_deduplicated_and_conflicting_duplicate_fails_closed() -> None:
    results = [
        result("supplier-a", EligibilityDecision.ELIGIBLE),
        result("supplier-a", EligibilityDecision.ELIGIBLE),
        result("supplier-b", EligibilityDecision.ELIGIBLE),
        result("supplier-b", EligibilityDecision.NEEDS_REFRESH),
    ]

    assert select_rfq_recipients(results, limit=10) == ["supplier-a"]


def test_empty_or_zero_selection_does_not_fill_from_noneligible_results() -> None:
    results = [
        result("supplier-refresh", EligibilityDecision.NEEDS_REFRESH),
        result("supplier-excluded", EligibilityDecision.EXCLUDED),
    ]

    assert select_rfq_recipients(results, limit=3) == []
    assert select_rfq_recipients([result("supplier-a", EligibilityDecision.ELIGIBLE)], 0) == []


def test_negative_selection_limit_is_rejected() -> None:
    with pytest.raises(ValueError, match="negative"):
        select_rfq_recipients([], limit=-1)


def test_result_rejects_eligible_decision_with_failed_or_missing_checks() -> None:
    failed_check = EligibilityCheck(
        criterion="invoice",
        required_value=True,
        actual_value=False,
        outcome=EligibilityOutcome.FAIL,
        reason_code="INVOICE_UNAVAILABLE",
    )

    with pytest.raises(ValidationError, match="decision must be derived"):
        SupplierEligibilityResult(
            supplier_id="sup_fabricated",
            decision=EligibilityDecision.ELIGIBLE,
            checks=[failed_check],
        )

    with pytest.raises(ValidationError):
        SupplierEligibilityResult(
            supplier_id="sup_fabricated",
            decision=EligibilityDecision.ELIGIBLE,
            checks=[],
        )
