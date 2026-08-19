from datetime import date, datetime, time
from zoneinfo import ZoneInfo

import pytest
from app.modules.procurement_requests import (
    MAX_BUYER_MESSAGE_CHARS,
    ProcurementRequestDraft,
    ProcurementRequestPatch,
    ProcurementRequestStatus,
)
from pydantic import ValidationError


def test_patch_distinguishes_omitted_from_explicit_false_and_zero() -> None:
    patch = ProcurementRequestPatch(
        invoice_required=False,
        vegetarian_count=0,
    )

    assert patch.model_fields_set == {"invoice_required", "vegetarian_count"}


def test_money_rejects_float_and_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        ProcurementRequestPatch(maximum_total_cents=4500.0)
    with pytest.raises(ValidationError):
        ProcurementRequestPatch.model_validate({"budget": 450_000})


def test_request_description_uses_shared_buyer_message_limit() -> None:
    assert (
        ProcurementRequestPatch(description="x" * MAX_BUYER_MESSAGE_CHARS).description
        == "x" * MAX_BUYER_MESSAGE_CHARS
    )
    with pytest.raises(ValidationError, match="at most 4000 characters"):
        ProcurementRequestPatch(description="x" * (MAX_BUYER_MESSAGE_CHARS + 1))


def test_request_status_is_a_domain_lifecycle_not_an_agent_stop_reason() -> None:
    assert ProcurementRequestStatus.NEEDS_CLARIFICATION.value == "NEEDS_CLARIFICATION"
    assert "MAX_STEPS_REACHED" not in {status.value for status in ProcurementRequestStatus}


def test_draft_requires_timezone_aware_response_deadline() -> None:
    with pytest.raises(ValidationError, match="timezone-aware"):
        ProcurementRequestDraft(
            request_id="pr_demo",
            response_deadline=datetime(2026, 8, 20, 18),
        )


def test_draft_accepts_critical_field_types() -> None:
    request = ProcurementRequestDraft(
        request_id="pr_demo",
        category="corporate_catering",
        description="Coffee break",
        event_date=date(2026, 8, 28),
        delivery_time=time(8, 30),
        people_count=80,
        response_deadline=datetime(
            2026,
            8,
            20,
            18,
            tzinfo=ZoneInfo("America/Sao_Paulo"),
        ),
    )

    assert request.status is ProcurementRequestStatus.DRAFT
