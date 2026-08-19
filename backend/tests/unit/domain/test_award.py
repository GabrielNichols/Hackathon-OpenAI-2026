from datetime import UTC, datetime

import pytest

from app.contracts import ActorType, AwardStatus, ErrorCode
from app.domain import DomainError
from app.domain.approvals import ApprovalAggregate
from app.domain.awards import AwardAggregate

NOW = datetime(2026, 8, 19, 15, 0, tzinfo=UTC)


def _approval(*, approved: bool) -> ApprovalAggregate:
    approval = ApprovalAggregate.create(
        approval_id="apr_alpha",
        tenant_id="org_demo",
        procurement_request_id="pr_demo",
        quote_id="quo_alpha",
        quote_version=3,
        approved_total_cents=420_000,
    )
    if approved:
        approval.approve(actor_type=ActorType.HUMAN, actor_id="approver_demo", decided_at=NOW)
    return approval


def _award() -> AwardAggregate:
    return AwardAggregate.create(
        award_id="awd_alpha",
        tenant_id="org_demo",
        procurement_request_id="pr_demo",
        supplier_id="sup_alpha",
        approval=_approval(approved=True),
        terms_snapshot={"total_cents": 420_000, "quote_version": 3},
    )


def test_award_cannot_be_created_without_valid_human_approval() -> None:
    with pytest.raises(DomainError) as exc_info:
        AwardAggregate.create(
            award_id="awd_alpha",
            tenant_id="org_demo",
            procurement_request_id="pr_demo",
            supplier_id="sup_alpha",
            approval=_approval(approved=False),
            terms_snapshot={"total_cents": 420_000},
        )

    assert exc_info.value.code is ErrorCode.POLICY_DENIED


def test_award_is_not_sent_until_delivery_ack() -> None:
    award = _award()

    with pytest.raises(DomainError) as exc_info:
        award.confirm_delivery(delivery_ack_id="", sent_at=NOW)

    assert exc_info.value.code is ErrorCode.EXTERNAL_DELIVERY_NOT_CONFIRMED
    assert award.state is AwardStatus.CREATED


def test_award_acceptance_requires_real_submission_and_matching_terms_hash() -> None:
    award = _award()
    award.confirm_delivery(delivery_ack_id="ack_award", sent_at=NOW)

    with pytest.raises(DomainError) as missing_submission:
        award.accept(
            submission_id="",
            respondent_id="contact_alpha",
            submitted_at=NOW,
            displayed_terms_hash=award.terms_hash,
            explicit_confirmation=True,
        )
    assert missing_submission.value.code is ErrorCode.VALIDATION_ERROR

    with pytest.raises(DomainError) as changed_terms:
        award.accept(
            submission_id="supplier_submission",
            respondent_id="contact_alpha",
            submitted_at=NOW,
            displayed_terms_hash="tampered",
            explicit_confirmation=True,
        )
    assert changed_terms.value.code is ErrorCode.LINK_INVALID
    assert award.state is AwardStatus.SENT


def test_award_acceptance_is_terminal() -> None:
    award = _award()
    award.confirm_delivery(delivery_ack_id="ack_award", sent_at=NOW)
    award.accept(
        submission_id="supplier_submission",
        respondent_id="contact_alpha",
        submitted_at=NOW,
        displayed_terms_hash=award.terms_hash,
        explicit_confirmation=True,
    )

    with pytest.raises(DomainError) as exc_info:
        award.decline(
            submission_id="supplier_submission_2",
            respondent_id="contact_alpha",
            submitted_at=NOW,
        )

    assert exc_info.value.code is ErrorCode.INVALID_STATE_TRANSITION
    assert award.state is AwardStatus.ACCEPTED
