from __future__ import annotations

import asyncio

import pytest

from app.modules.procurement_agent.demo import create_demo_container
from app.modules.procurement_agent.models import AgentStopReason
from app.modules.procurement_requests import InterpretationProviderError
from app.modules.procurement_requests.schemas import (
    MAX_BUYER_MESSAGE_CHARS,
    ProcurementRequestStatus,
)

COMPLETE_MESSAGE = (
    "Preciso de um coffee break para 80 pessoas em 21/08/2026, entregue às 8h30 "
    "na Vila Olímpia, São Paulo. Serão 12 vegetarianos, 4 veganos e 3 sem glúten. "
    "Orçamento máximo de R$ 4.500, com nota fiscal obrigatória e sem plásticos "
    "descartáveis. Quero 3 cotações, respostas até 20/08/2026 às 18h. "
    "Aprovador: approver_demo."
)


def incomplete_message(people: int = 80) -> str:
    return (
        f"Coffee break para {people} pessoas em 21/08/2026, entrega às 8h30. "
        "Orçamento de R$ 4.500, nota fiscal obrigatória e sem plásticos. "
        "Respostas até 20/08/2026 às 18h. Aprovador: approver_demo."
    )


async def test_missing_location_requests_clarification_and_does_not_source() -> None:
    container = create_demo_container()

    view = await container.orchestrator.receive_message(incomplete_message())

    assert view.status is ProcurementRequestStatus.NEEDS_CLARIFICATION
    assert view.stop_reason is AgentStopReason.NEEDS_CLARIFICATION
    assert view.missing_fields == ["location_district"]
    assert "bairro" in (view.clarification_question or "").lower()
    assert container.directory.query_count == 0
    assert container.rfq.create_call_count == 0


async def test_clarification_promotes_request_to_ready_without_starting_sourcing() -> None:
    container = create_demo_container()
    first = await container.orchestrator.receive_message(incomplete_message())

    ready = await container.orchestrator.receive_message(
        "Será na Vila Olímpia, em São Paulo.",
        request_id=first.request_id,
    )

    assert ready.status is ProcurementRequestStatus.READY
    assert ready.stop_reason is AgentStopReason.AWAITING_PLAN_CONFIRMATION
    assert ready.plan is not None
    assert ready.draft["maximum_total_cents"] == 450_000
    assert container.directory.query_count == 0


async def test_conflicting_people_count_is_not_silently_overwritten() -> None:
    container = create_demo_container()
    first = await container.orchestrator.receive_message(incomplete_message(80))

    conflicted = await container.orchestrator.receive_message(
        "Corrigindo: serão 100 pessoas na Vila Olímpia, São Paulo.",
        request_id=first.request_id,
    )

    assert conflicted.status is ProcurementRequestStatus.NEEDS_CLARIFICATION
    assert conflicted.draft["people_count"] == 80
    assert "80" in (conflicted.clarification_question or "")
    assert "100" in (conflicted.clarification_question or "")
    assert container.directory.query_count == 0

    resolved = await container.orchestrator.receive_message(
        "Confirmo 100 pessoas.",
        request_id=first.request_id,
    )

    assert resolved.status is ProcurementRequestStatus.READY
    assert resolved.draft["people_count"] == 100
    assert resolved.stop_reason is AgentStopReason.AWAITING_PLAN_CONFIRMATION
    assert any(event.event_type == "PROCUREMENT_FIELD_CONFIRMED" for event in resolved.timeline)


@pytest.mark.parametrize(
    "confirmation",
    [
        "Não confirmo 100 pessoas.",
        "Confirmo que não serão 100 pessoas.",
    ],
)
async def test_negated_confirmation_cannot_overwrite_conflicting_value(
    confirmation: str,
) -> None:
    container = create_demo_container()
    first = await container.orchestrator.receive_message(incomplete_message(80))
    await container.orchestrator.receive_message(
        "Corrigindo: serão 100 pessoas na Vila Olímpia, São Paulo.",
        request_id=first.request_id,
    )

    rejected = await container.orchestrator.receive_message(
        confirmation,
        request_id=first.request_id,
    )

    assert rejected.status is ProcurementRequestStatus.NEEDS_CLARIFICATION
    assert rejected.draft["people_count"] == 80
    assert not any(event.event_type == "PROCUREMENT_FIELD_CONFIRMED" for event in rejected.timeline)


async def test_provider_failure_retry_reuses_creation_aggregate(monkeypatch) -> None:
    container = create_demo_container()
    original_interpret = container.interpreter.interpret
    calls = 0

    async def fail_once(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise InterpretationProviderError("OPENAI_INTERPRETATION_FAILED")
        return await original_interpret(*args, **kwargs)

    monkeypatch.setattr(container.interpreter, "interpret", fail_once)
    with pytest.raises(InterpretationProviderError):
        await container.orchestrator.receive_message(
            COMPLETE_MESSAGE,
            idempotency_key="provider-retry-key",
        )
    first_created = [
        event
        for event in container.audit.events
        if event.event_type == "PROCUREMENT_REQUEST_CREATED"
    ]

    retried = await container.orchestrator.receive_message(
        COMPLETE_MESSAGE,
        idempotency_key="provider-retry-key",
    )

    assert retried.request_id == first_created[0].aggregate_id
    assert retried.status is ProcurementRequestStatus.READY
    assert (
        len(
            [
                event
                for event in container.audit.events
                if event.event_type == "PROCUREMENT_REQUEST_CREATED"
            ]
        )
        == 1
    )
    assert calls == 2


async def test_oversized_message_is_rejected_before_provider_or_idempotency_reservation(
    monkeypatch,
) -> None:
    container = create_demo_container()
    original_interpret = container.interpreter.interpret
    calls = 0

    async def tracked_interpret(*args, **kwargs):
        nonlocal calls
        calls += 1
        return await original_interpret(*args, **kwargs)

    monkeypatch.setattr(container.interpreter, "interpret", tracked_interpret)
    with pytest.raises(ValueError, match="message cannot exceed 4000 characters"):
        await container.orchestrator.receive_message(
            "x" * (MAX_BUYER_MESSAGE_CHARS + 1),
            idempotency_key="oversized-message-key",
        )

    assert calls == 0
    assert not container.audit.events

    accepted = await container.orchestrator.receive_message(
        COMPLETE_MESSAGE,
        idempotency_key="oversized-message-key",
    )

    assert accepted.status is ProcurementRequestStatus.READY
    assert calls == 1


async def test_unexpected_interpreter_failure_is_saved_and_retried_without_masking(
    monkeypatch,
) -> None:
    container = create_demo_container()
    original_interpret = container.interpreter.interpret
    failure = RuntimeError("unexpected interpreter failure")
    calls = 0

    async def fail_once(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise failure
        return await original_interpret(*args, **kwargs)

    monkeypatch.setattr(container.interpreter, "interpret", fail_once)
    with pytest.raises(RuntimeError) as caught:
        await container.orchestrator.receive_message(
            COMPLETE_MESSAGE,
            idempotency_key="unexpected-interpreter-retry-key",
        )

    assert caught.value is failure
    created_events = [
        event
        for event in container.audit.events
        if event.event_type == "PROCUREMENT_REQUEST_CREATED"
    ]
    assert len(created_events) == 1
    request_id = created_events[0].aggregate_id
    blocked = await container.orchestrator.get(request_id)
    assert blocked.status is ProcurementRequestStatus.DRAFT
    assert blocked.stop_reason is AgentStopReason.ACTION_BLOCKED
    assert any(
        event.event_type == "AGENT_RUN_COMPLETED"
        and event.payload["stop_reason"] == AgentStopReason.ACTION_BLOCKED
        for event in blocked.timeline
    )

    retried = await container.orchestrator.receive_message(
        COMPLETE_MESSAGE,
        idempotency_key="unexpected-interpreter-retry-key",
    )

    assert retried.request_id == request_id
    assert retried.status is ProcurementRequestStatus.READY
    assert calls == 2
    assert (
        len(
            [
                event
                for event in container.audit.events
                if event.event_type == "PROCUREMENT_REQUEST_CREATED"
            ]
        )
        == 1
    )


async def test_confirmed_plan_runs_explainable_sourcing_and_creates_draft_only() -> None:
    container = create_demo_container()
    ready = await container.orchestrator.receive_message(COMPLETE_MESSAGE)

    sourced = await container.orchestrator.confirm_plan(ready.request_id)

    assert sourced.status is ProcurementRequestStatus.SOURCING
    assert sourced.stop_reason is AgentStopReason.AWAITING_EXTERNAL_RESPONSE
    assert sourced.selected_supplier_ids == ["sup_alpha", "sup_beta"]
    assert sourced.rfq_round_id is not None
    decisions = {result.supplier_id: result.decision for result in sourced.eligibility_results}
    assert decisions == {
        "sup_alpha": "eligible",
        "sup_atualizar": "needs_refresh",
        "sup_beta": "eligible",
        "sup_fora_area": "excluded",
        "sup_sem_nf": "excluded",
    }
    event_types = [event.event_type for event in sourced.timeline]
    assert "RFQ_ROUND_DRAFT_CREATED" in event_types
    assert "RFQ_DELIVERY_CONFIRMED" not in event_types
    assert sourced.status is not ProcurementRequestStatus.RFQ_ACTIVE


async def test_repeated_plan_confirmation_does_not_duplicate_external_command() -> None:
    container = create_demo_container()
    ready = await container.orchestrator.receive_message(COMPLETE_MESSAGE)

    first = await container.orchestrator.confirm_plan(ready.request_id)
    second = await container.orchestrator.confirm_plan(ready.request_id)

    assert first.rfq_round_id == second.rfq_round_id
    assert container.directory.query_count == 1
    assert container.rfq.create_call_count == 1


async def test_vertical_policy_supplies_default_city_without_fabricating_buyer_fact() -> None:
    container = create_demo_container()
    district_only = COMPLETE_MESSAGE.replace(", São Paulo", "")

    ready = await container.orchestrator.receive_message(district_only)
    sourced = await container.orchestrator.confirm_plan(ready.request_id)

    assert ready.draft["location_city"] is None
    assert ready.plan is not None
    assert ready.plan.policy_snapshot.default_location_city == "São Paulo"
    assert sourced.selected_supplier_ids == ["sup_alpha", "sup_beta"]


async def test_concurrent_plan_confirmation_keeps_rfq_side_effect_idempotent() -> None:
    container = create_demo_container()
    ready = await container.orchestrator.receive_message(COMPLETE_MESSAGE)

    first, second = await asyncio.gather(
        container.orchestrator.confirm_plan(ready.request_id),
        container.orchestrator.confirm_plan(ready.request_id),
    )

    assert first.rfq_round_id == second.rfq_round_id
    assert container.rfq.create_call_count == 1


async def test_max_steps_stops_before_external_side_effect() -> None:
    container = create_demo_container(max_steps=1)
    ready = await container.orchestrator.receive_message(COMPLETE_MESSAGE)

    stopped = await container.orchestrator.confirm_plan(ready.request_id)

    assert stopped.status is ProcurementRequestStatus.READY
    assert stopped.stop_reason is AgentStopReason.MAX_STEPS_REACHED
    assert stopped.rfq_round_id is None
    assert container.rfq.create_call_count == 0


async def test_policy_denial_stops_and_audits_before_sourcing() -> None:
    container = create_demo_container(denied_actions={"start_sourcing"})
    ready = await container.orchestrator.receive_message(COMPLETE_MESSAGE)

    blocked = await container.orchestrator.confirm_plan(ready.request_id)

    assert blocked.status is ProcurementRequestStatus.READY
    assert blocked.stop_reason is AgentStopReason.ACTION_BLOCKED
    assert container.directory.query_count == 0
    assert any(event.event_type == "AGENT_ACTION_BLOCKED" for event in blocked.timeline)


async def test_no_eligible_suppliers_moves_to_explicit_alternate_state() -> None:
    container = create_demo_container(suppliers=[])
    ready = await container.orchestrator.receive_message(COMPLETE_MESSAGE)

    empty = await container.orchestrator.confirm_plan(ready.request_id)

    assert empty.status is ProcurementRequestStatus.NO_ELIGIBLE_SUPPLIERS
    assert empty.stop_reason is AgentStopReason.NO_ELIGIBLE_SUPPLIERS
    assert empty.rfq_round_id is None


async def test_directory_failure_finishes_run_and_leaves_request_retryable(monkeypatch) -> None:
    container = create_demo_container()
    ready = await container.orchestrator.receive_message(COMPLETE_MESSAGE)
    original_search = container.directory.search

    async def failing_search(_criteria):
        raise RuntimeError("directory unavailable")

    monkeypatch.setattr(container.directory, "search", failing_search)
    failed = await container.orchestrator.confirm_plan(ready.request_id)

    assert failed.status is ProcurementRequestStatus.READY
    assert failed.stop_reason is AgentStopReason.ACTION_BLOCKED
    assert any(event.event_type == "AGENT_TOOL_FAILED" for event in failed.timeline)
    assert any(event.event_type == "SOURCING_FAILED" for event in failed.timeline)

    monkeypatch.setattr(container.directory, "search", original_search)
    retried = await container.orchestrator.confirm_plan(ready.request_id)
    assert retried.stop_reason is AgentStopReason.AWAITING_EXTERNAL_RESPONSE
    assert container.rfq.create_call_count == 1


async def test_concurrent_clarifications_do_not_lose_request_updates(monkeypatch) -> None:
    container = create_demo_container()
    initial = await container.orchestrator.receive_message(
        "Coffee break em 21/08/2026, entrega às 8h30. Orçamento de R$ 4.500, "
        "nota fiscal obrigatória e sem plásticos. Respostas até 20/08/2026 às 18h. "
        "Aprovador: approver_demo."
    )
    original_interpret = container.interpreter.interpret

    async def delayed_interpret(*args, **kwargs):
        await asyncio.sleep(0.01)
        return await original_interpret(*args, **kwargs)

    monkeypatch.setattr(container.interpreter, "interpret", delayed_interpret)
    await asyncio.gather(
        container.orchestrator.receive_message(
            "Serão 80 pessoas.",
            request_id=initial.request_id,
        ),
        container.orchestrator.receive_message(
            "Na Vila Olímpia, em São Paulo.",
            request_id=initial.request_id,
        ),
    )

    final = await container.orchestrator.get(initial.request_id)
    assert final.draft["people_count"] == 80
    assert final.draft["location_district"] == "Vila Olímpia"
    assert final.draft["version"] == 4
