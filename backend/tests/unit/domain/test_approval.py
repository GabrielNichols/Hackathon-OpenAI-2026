from datetime import UTC, datetime

import pytest

from app.contracts import ActorType, ApprovalStatus, ErrorCode
from app.domain import DomainError
from app.domain.approvals import ApprovalAggregate

NOW = datetime(2026, 8, 19, 15, 0, tzinfo=UTC)


def _approval() -> ApprovalAggregate:
    return ApprovalAggregate.create(
        approval_id="apr_alpha",
        tenant_id="org_demo",
        procurement_request_id="pr_demo",
        quote_id="quo_alpha",
        quote_version=3,
        approved_total_cents=420_000,
    )


def test_agent_cannot_approve_spend() -> None:
    approval = _approval()

    with pytest.raises(DomainError) as exc_info:
        approval.approve(actor_type=ActorType.AGENT, actor_id="agent_demo", decided_at=NOW)

    assert exc_info.value.code is ErrorCode.POLICY_DENIED
    assert approval.state is ApprovalStatus.REQUESTED


def test_approval_is_bound_to_exact_quote_version() -> None:
    approval = _approval()
    approval.approve(actor_type=ActorType.HUMAN, actor_id="approver_demo", decided_at=NOW)

    with pytest.raises(DomainError) as exc_info:
        approval.assert_valid_for(quote_id="quo_alpha", quote_version=4)

    assert exc_info.value.code is ErrorCode.CONFLICT


def test_approval_decision_is_terminal() -> None:
    approval = _approval()
    approval.approve(actor_type=ActorType.HUMAN, actor_id="approver_demo", decided_at=NOW)

    with pytest.raises(DomainError) as exc_info:
        approval.reject(
            actor_type=ActorType.HUMAN,
            actor_id="approver_demo",
            decided_at=NOW,
            reason="changed mind",
        )

    assert exc_info.value.code is ErrorCode.INVALID_STATE_TRANSITION
    assert approval.state is ApprovalStatus.APPROVED


def test_rejection_requires_reason() -> None:
    approval = _approval()

    with pytest.raises(DomainError) as exc_info:
        approval.reject(
            actor_type=ActorType.HUMAN,
            actor_id="approver_demo",
            decided_at=NOW,
            reason="",
        )

    assert exc_info.value.code is ErrorCode.VALIDATION_ERROR
    assert approval.state is ApprovalStatus.REQUESTED
