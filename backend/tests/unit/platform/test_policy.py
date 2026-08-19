from datetime import UTC, datetime

import pytest

from app.contracts import ActorType, AuthorizationRequest
from app.platform import AuditedPolicyEngine, DeterministicPolicyEngine, FixedClock
from app.platform.ids import SequenceIdGenerator


def request(**overrides: object) -> AuthorizationRequest:
    values: dict[str, object] = {
        "actor_type": ActorType.AGENT,
        "actor_id": "usr_agent",
        "actor_tenant_id": "org_buyer",
        "resource_tenant_id": "org_buyer",
        "action": "start_sourcing",
        "aggregate_type": "procurement_request",
        "aggregate_id": "pr_demo",
        "current_state": "READY",
        "arguments": {},
        "procurement_policy": {},
    }
    values.update(overrides)
    return AuthorizationRequest.model_validate(values)


@pytest.mark.asyncio
async def test_policy_blocks_agent_from_approving_spend() -> None:
    decision = await DeterministicPolicyEngine().authorize(request(action="approve_spend"))
    assert not decision.allowed
    assert decision.reason_code == "AGENT_CANNOT_APPROVE_SPEND"
    assert decision.requires_human_review


@pytest.mark.asyncio
async def test_policy_blocks_budget_above_maximum() -> None:
    decision = await DeterministicPolicyEngine().authorize(
        request(
            action="create_rfq_round",
            arguments={"total_cents": 450_001},
            procurement_policy={"maximum_total_cents": 450_000},
        )
    )
    assert decision.reason_code == "BUDGET_EXCEEDED"
    assert decision.constraints == {"maximum_total_cents": 450_000}


@pytest.mark.asyncio
async def test_policy_blocks_changing_mandatory_requirement() -> None:
    decision = await DeterministicPolicyEngine().authorize(
        request(
            action="change_mandatory_requirement",
            arguments={"changes": {"mandatory_requirements": ["invoice_required"]}},
            procurement_policy={"mandatory_requirements": ["invoice_required"]},
        )
    )
    assert decision.reason_code == "MANDATORY_REQUIREMENT_CHANGE_FORBIDDEN"


@pytest.mark.asyncio
async def test_policy_blocks_cross_tenant_access() -> None:
    decision = await DeterministicPolicyEngine().authorize(request(resource_tenant_id="org_other"))
    assert decision.reason_code == "CROSS_TENANT_ACCESS"


@pytest.mark.asyncio
async def test_policy_requires_fields_before_sourcing() -> None:
    decision = await DeterministicPolicyEngine().authorize(
        request(
            arguments={"present_fields": ["category"]},
            procurement_policy={"required_fields_before_sourcing": ["category", "event_date"]},
        )
    )
    assert decision.reason_code == "MISSING_REQUIRED_FIELDS"
    assert decision.constraints == {"missing_fields": ["event_date"]}


class AuditFake:
    def __init__(self) -> None:
        self.events = []

    async def append(self, events: object) -> None:
        self.events.extend(events)


@pytest.mark.asyncio
async def test_policy_denial_is_audited_without_state_change() -> None:
    audit = AuditFake()
    engine = AuditedPolicyEngine(
        DeterministicPolicyEngine(),
        audit,
        FixedClock(datetime(2026, 8, 19, 15, tzinfo=UTC)),
        SequenceIdGenerator(),
    )
    decision = await engine.authorize(request(action="approve_spend"))
    assert not decision.allowed
    assert len(audit.events) == 1
    event = audit.events[0]
    assert event.event_type == "AGENT_ACTION_BLOCKED"
    assert event.previous_state == event.new_state == "READY"
