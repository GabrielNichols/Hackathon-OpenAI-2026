from datetime import UTC, datetime, timedelta

import pytest

from app.contracts import ErrorCode, QuoteState
from app.domain import DomainError
from app.domain.quotes import QuoteAggregate

NOW = datetime(2026, 8, 19, 15, 0, tzinfo=UTC)


def _valid_facts() -> dict[str, object]:
    return {
        "total_cents": 420_000,
        "availability": True,
        "items": ("coffee_break",),
        "valid_until": NOW + timedelta(days=1),
        "requirements": {"invoice_required": "met"},
        "respondent_id": "contact_alpha",
        "validated_at": NOW,
    }


def _validating_quote() -> QuoteAggregate:
    quote = QuoteAggregate.create(
        quote_id="quo_alpha",
        tenant_id="org_demo",
        procurement_request_id="pr_demo",
        supplier_id="sup_alpha",
        rfq_round_id="rfq_demo",
    )
    quote.record_opened(open_event_id="open_quote")
    quote.save_draft()
    quote.submit(submission_id="submit_quote", respondent_id="contact_alpha")
    quote.start_validation()
    return quote


@pytest.mark.parametrize(
    ("field", "invalid"),
    [
        ("total_cents", None),
        ("availability", None),
        ("items", ()),
        ("valid_until", None),
        ("requirements", {}),
        ("respondent_id", ""),
    ],
)
def test_quote_cannot_be_valid_without_required_fields(field: str, invalid: object) -> None:
    quote = _validating_quote()
    facts = _valid_facts()
    facts[field] = invalid

    with pytest.raises(DomainError) as exc_info:
        quote.mark_valid(**facts)  # type: ignore[arg-type]

    assert exc_info.value.code is ErrorCode.VALIDATION_ERROR
    assert quote.state is QuoteState.VALIDATING


@pytest.mark.parametrize("invalid", [-1, 1.5, True, False])
def test_quote_rejects_float_bool_or_negative_money(invalid: object) -> None:
    quote = _validating_quote()
    facts = _valid_facts()
    facts["total_cents"] = invalid

    with pytest.raises(DomainError) as exc_info:
        quote.mark_valid(**facts)  # type: ignore[arg-type]

    assert exc_info.value.code is ErrorCode.VALIDATION_ERROR


def test_quote_revalidates_after_clarification() -> None:
    quote = _validating_quote()
    quote.request_clarification(reason="missing delivery details")
    quote.record_clarification(submission_id="clarification_submission")
    quote.mark_valid(**_valid_facts())  # type: ignore[arg-type]

    assert quote.state is QuoteState.VALID


def test_quote_can_finalize_without_negotiation() -> None:
    quote = _validating_quote()
    quote.mark_valid(**_valid_facts())  # type: ignore[arg-type]

    quote.finalize()

    assert quote.state is QuoteState.FINAL
