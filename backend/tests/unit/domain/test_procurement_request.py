from datetime import UTC, datetime

import pytest

from app.contracts import ActorType, ErrorCode, ProcurementRequestState
from app.domain import DomainError
from app.domain.procurement import REQUIRED_PROCUREMENT_FIELDS, ProcurementRequestAggregate

NOW = datetime(2026, 8, 19, 15, 0, tzinfo=UTC)


def _sourcing_request() -> ProcurementRequestAggregate:
    request = ProcurementRequestAggregate.create(request_id="pr_demo", tenant_id="org_demo")
    request.mark_ready(provided_fields=REQUIRED_PROCUREMENT_FIELDS)
    request.start_sourcing()
    return request


def _awaiting_approval_request() -> ProcurementRequestAggregate:
    request = _sourcing_request()
    request.mark_rfq_active(delivery_ack_id="ack_rfq")
    request.begin_quote_review(submission_id="submission_quote")
    request.request_approval(quote_id="quo_alpha", quote_version=3)
    return request


def _award_sent_request() -> ProcurementRequestAggregate:
    request = _awaiting_approval_request()
    request.record_human_approval(
        approval_id="apr_alpha",
        actor_type=ActorType.HUMAN,
        actor_id="approver_demo",
        quote_id="quo_alpha",
        quote_version=3,
    )
    request.record_award_sent(
        award_id="awd_alpha",
        delivery_ack_id="ack_award",
        quote_id="quo_alpha",
        quote_version=3,
        terms_hash="terms-v3",
    )
    return request


def test_procurement_request_requires_ready_before_sourcing() -> None:
    request = ProcurementRequestAggregate.create(request_id="pr_demo", tenant_id="org_demo")

    with pytest.raises(DomainError) as exc_info:
        request.start_sourcing()

    assert exc_info.value.code is ErrorCode.INVALID_STATE_TRANSITION
    assert request.state is ProcurementRequestState.DRAFT


def test_rfq_active_requires_delivery_ack() -> None:
    request = _sourcing_request()

    with pytest.raises(DomainError) as exc_info:
        request.mark_rfq_active(delivery_ack_id="")

    assert exc_info.value.code is ErrorCode.EXTERNAL_DELIVERY_NOT_CONFIRMED
    assert request.state is ProcurementRequestState.SOURCING


def test_procurement_request_cannot_award_without_human_approval() -> None:
    request = _awaiting_approval_request()

    with pytest.raises(DomainError) as exc_info:
        request.record_award_sent(
            award_id="awd_alpha",
            delivery_ack_id="ack_award",
            quote_id="quo_alpha",
            quote_version=3,
            terms_hash="terms-v3",
        )

    assert exc_info.value.code is ErrorCode.INVALID_STATE_TRANSITION
    assert request.state is ProcurementRequestState.AWAITING_APPROVAL


def test_agent_cannot_record_human_approval() -> None:
    request = _awaiting_approval_request()

    with pytest.raises(DomainError) as exc_info:
        request.record_human_approval(
            approval_id="apr_alpha",
            actor_type=ActorType.AGENT,
            actor_id="agent_demo",
            quote_id="quo_alpha",
            quote_version=3,
        )

    assert exc_info.value.code is ErrorCode.POLICY_DENIED
    assert request.state is ProcurementRequestState.AWAITING_APPROVAL


def test_supplier_acceptance_requires_real_submission_event() -> None:
    request = _award_sent_request()

    with pytest.raises(DomainError) as exc_info:
        request.record_supplier_acceptance(
            award_id="awd_alpha",
            submission_id="",
            terms_hash="terms-v3",
        )

    assert exc_info.value.code is ErrorCode.VALIDATION_ERROR
    assert request.state is ProcurementRequestState.AWARD_SENT


def test_ready_for_contracting_requires_confirmed_capacity_reservation() -> None:
    request = _award_sent_request()
    request.record_supplier_acceptance(
        award_id="awd_alpha",
        submission_id="supplier_submission",
        terms_hash="terms-v3",
    )

    with pytest.raises(DomainError) as exc_info:
        request.mark_ready_for_contracting(reservation_id="", confirmed_at=NOW)

    assert exc_info.value.code is ErrorCode.VALIDATION_ERROR
    assert request.state is ProcurementRequestState.SUPPLIER_ACCEPTED
