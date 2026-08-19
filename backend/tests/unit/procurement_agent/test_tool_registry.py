from datetime import UTC, datetime

import pytest
from pydantic import BaseModel, ConfigDict

from app.modules.buyer_timeline.audit import InMemoryAuditLog
from app.modules.procurement_agent.adapters import (
    FixedClock,
    PrototypePolicy,
    SequentialIdGenerator,
)
from app.modules.procurement_agent.models import ToolExecutionContext
from app.modules.procurement_agent.tool_registry import (
    ToolInputError,
    ToolPolicyDeniedError,
    ToolRegistry,
    ToolSpec,
    ToolStateError,
)


class EchoInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    value: str


class EchoOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    echoed: str


def make_registry(*, denied_actions: set[str] | None = None):
    audit = InMemoryAuditLog()
    clock = FixedClock(datetime(2026, 8, 19, 15, 0, tzinfo=UTC))
    ids = SequentialIdGenerator()
    registry = ToolRegistry(
        policy=PrototypePolicy(denied_actions=denied_actions),
        audit=audit,
        clock=clock,
        ids=ids,
    )

    async def echo(arguments: BaseModel) -> BaseModel:
        typed = EchoInput.model_validate(arguments)
        return EchoOutput(echoed=typed.value)

    registry.register(
        ToolSpec(
            name="echo",
            input_model=EchoInput,
            output_model=EchoOutput,
            allowed_states=frozenset({"SOURCING"}),
            policy_action="search_suppliers",
            audit_event_type="ECHO_EXECUTED",
            timeout_seconds=1,
            idempotent=True,
            handler=echo,
        )
    )
    return registry, audit


def context(state: str = "SOURCING") -> ToolExecutionContext:
    return ToolExecutionContext(
        aggregate_id="pr_demo",
        aggregate_state=state,
        actor_id="agent_demo",
        correlation_id="cor_demo",
        agent_run_id="run_demo",
        procurement_policy={},
    )


@pytest.mark.asyncio
async def test_tool_arguments_reject_extra_fields_before_execution() -> None:
    registry, audit = make_registry()

    with pytest.raises(ToolInputError):
        await registry.execute("echo", {"value": "ok", "unexpected": True}, context())

    assert audit.events[-1].event_type == "AGENT_ACTION_BLOCKED"
    assert audit.events[-1].payload["reason_code"] == "VALIDATION_ERROR"


@pytest.mark.asyncio
async def test_tool_is_blocked_outside_allowlisted_state() -> None:
    registry, audit = make_registry()

    with pytest.raises(ToolStateError):
        await registry.execute("echo", {"value": "ok"}, context("READY"))

    assert audit.events[-1].payload["reason_code"] == "TOOL_NOT_ALLOWED_IN_STATE"


@pytest.mark.asyncio
async def test_policy_denial_stops_execution_and_is_audited() -> None:
    registry, audit = make_registry(denied_actions={"search_suppliers"})

    with pytest.raises(ToolPolicyDeniedError):
        await registry.execute("echo", {"value": "ok"}, context())

    assert [event.event_type for event in audit.events] == ["AGENT_ACTION_BLOCKED"]


@pytest.mark.asyncio
async def test_authorized_tool_has_typed_output_and_real_audit_events() -> None:
    registry, audit = make_registry()

    result = await registry.execute("echo", {"value": "olá"}, context())

    assert result.output == {"echoed": "olá"}
    assert [event.event_type for event in audit.events] == [
        "AGENT_TOOL_AUTHORIZED",
        "ECHO_EXECUTED",
        "AGENT_TOOL_EXECUTED",
    ]
