from datetime import UTC, datetime

import pytest

from app.modules.procurement_agent.adapters import (
    FixedClock,
    IdempotencyConflictError,
    InMemoryRFQExecutionAdapter,
    SequentialIdGenerator,
)
from app.modules.procurement_agent.models import CommandContext, CreateRFQRoundCommand


def command(
    *,
    run_id: str,
    correlation_id: str,
    recipients: list[str] | None = None,
) -> CreateRFQRoundCommand:
    return CreateRFQRoundCommand(
        procurement_request_id="pr_demo",
        request_version=2,
        plan_version=1,
        sourcing_run_id=run_id,
        recipient_supplier_ids=recipients or ["sup_alpha"],
        response_deadline=datetime(2026, 8, 20, 21, 0, tzinfo=UTC),
        requirements_snapshot={"people_count": 80},
        policy_snapshot={"version": 1},
        context=CommandContext(
            tenant_id="org_demo",
            actor_type="agent",
            actor_id="procurement_agent",
            correlation_id=correlation_id,
            agent_run_id=run_id,
            idempotency_key="rfq:create:pr_demo:v2:plan:1",
        ),
    )


async def test_same_semantic_command_is_idempotent_across_agent_run_retries() -> None:
    adapter = InMemoryRFQExecutionAdapter(
        clock=FixedClock(datetime(2026, 8, 19, 15, 0, tzinfo=UTC)),
        ids=SequentialIdGenerator(),
    )

    first = await adapter.create_round(command(run_id="run_1", correlation_id="cor_1"))
    retry = await adapter.create_round(command(run_id="run_2", correlation_id="cor_2"))

    assert retry.rfq_round_id == first.rfq_round_id
    assert adapter.create_call_count == 1


async def test_same_idempotency_key_rejects_different_semantic_payload() -> None:
    adapter = InMemoryRFQExecutionAdapter(
        clock=FixedClock(datetime(2026, 8, 19, 15, 0, tzinfo=UTC)),
        ids=SequentialIdGenerator(),
    )
    await adapter.create_round(command(run_id="run_1", correlation_id="cor_1"))

    with pytest.raises(IdempotencyConflictError):
        await adapter.create_round(
            command(
                run_id="run_2",
                correlation_id="cor_2",
                recipients=["sup_beta"],
            )
        )
