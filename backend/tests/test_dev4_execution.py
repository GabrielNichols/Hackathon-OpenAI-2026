from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime, timedelta

import pytest
from app.bootstrap import create_execution_service
from app.modules.rfq.contracts import (
    CommandContextDTO,
    CompareQuotesCommand,
    CreateRFQRoundCommand,
    ExecutionPolicySnapshotDTO,
    QuoteSubmissionDTO,
    RequestApprovalCommand,
    RFQRequirementsSnapshotDTO,
    SendAwardCommand,
    SendRFQRoundCommand,
)
from app.shared.errors import DomainError

NOW = datetime(2026, 8, 19, 15, 0, tzinfo=UTC)


def context(key: str, *, actor_type: str = "agent", actor_id: str = "agent_demo"):
    return CommandContextDTO(
        tenant_id="org_demo",
        idempotency_key=key,
        correlation_id="cor_demo",
        actor_type=actor_type,
        actor_id=actor_id,
        agent_run_id="run_demo" if actor_type == "agent" else None,
    )


def requirements() -> RFQRequirementsSnapshotDTO:
    return RFQRequirementsSnapshotDTO(
        description="Coffee break corporativo para 80 pessoas",
        category="corporate_catering",
        event_date="2026-08-22",
        delivery_time="08:30",
        timezone="America/Sao_Paulo",
        location_city="Sao Paulo",
        location_district="Vila Olimpia",
        people_count=80,
        maximum_total_cents=450_000,
        vegetarian_count=12,
        vegan_count=4,
        gluten_free_count=3,
        invoice_required=True,
        no_single_use_plastic=True,
        mandatory_requirements=["invoice", "dietary_restrictions"],
    )


def policy() -> ExecutionPolicySnapshotDTO:
    return ExecutionPolicySnapshotDTO(
        source_policy_version=1,
        minimum_confirmed_deliveries=1,
        maximum_total_cents=450_000,
        ranking_weights={
            "price": 35,
            "restrictions": 20,
            "adequacy": 15,
            "logistics": 10,
            "response": 5,
            "sustainability": 5,
            "documentation": 5,
            "history": 5,
        },
        approver_user_id="buyer_gabriel",
    )


def create_command(key: str = "idem_create") -> CreateRFQRoundCommand:
    return CreateRFQRoundCommand(
        context=context(key),
        procurement_request_id="pr_demo_coffee_break",
        request_version=1,
        plan_version=1,
        recipient_supplier_ids=["supplier_alpha", "supplier_beta"],
        response_deadline=NOW + timedelta(hours=3),
        requirements=requirements(),
        execution_policy=policy(),
    )


def quote(total_cents: int, supplier_name: str) -> QuoteSubmissionDTO:
    delivery_fee = 20_000
    return QuoteSubmissionDTO(
        availability_confirmed=True,
        subtotal_cents=total_cents - delivery_fee,
        delivery_fee_cents=delivery_fee,
        other_fee_cents=0,
        total_cents=total_cents,
        included_items=["cafe", "salgados", "frutas"],
        substitutions=[],
        invoice_available=True,
        vegetarian_status="confirmed",
        vegan_status="confirmed",
        gluten_free_status="confirmed",
        cross_contamination_warning="producao separada sem certificacao",
        valid_until=NOW + timedelta(hours=2),
        cancellation_terms="Cancelamento sem custo ate 24h antes",
        respondent_name=supplier_name,
        respondent_contact=f"{supplier_name.lower()}@example.test",
        supplier_confirmation=True,
        sustainability_score=5,
        history_score=4,
        response_time_minutes=10,
    )


@pytest.mark.asyncio
async def test_create_round_freezes_snapshot_and_is_idempotent():
    service = create_execution_service(now=NOW)
    command = create_command()
    original_requirements = deepcopy(command.requirements.model_dump())

    first = await service.create_round(command)
    replay = await service.create_round(command)

    assert first.rfq_round_id == replay.rfq_round_id
    assert replay.idempotent_replay is True
    assert first.requirements_snapshot_hash == replay.requirements_snapshot_hash
    assert service.get_round_requirements(first.rfq_round_id) == original_requirements

    conflicting = create_command()
    conflicting.recipient_supplier_ids = ["supplier_alpha"]
    with pytest.raises(DomainError, match="IDEMPOTENCY_CONFLICT"):
        await service.create_round(conflicting)


@pytest.mark.asyncio
async def test_delivery_requires_ack_and_retry_does_not_duplicate():
    service = create_execution_service(now=NOW, auto_ack=False)
    created = await service.create_round(create_command())
    command = SendRFQRoundCommand(
        context=context("idem_send"),
        rfq_round_id=created.rfq_round_id,
        expected_round_version=created.round_version,
        channel="manual_link",
    )

    pending = await service.send_round(command)
    replay = await service.send_round(command)

    assert pending.confirmed_count == 0
    assert pending.activation_criteria_met is False
    assert replay.idempotent_replay is True
    assert len(service.delivery_gateway.messages) == 2

    service.delivery_gateway.ack(service.delivery_gateway.messages[0].external_id)
    delivered = await service.get_delivery_status(created.rfq_round_id)
    assert delivered.confirmed_count == 1
    assert delivered.activation_criteria_met is True
    assert delivered.all_confirmed is False


@pytest.mark.asyncio
async def test_quote_is_recalculated_and_response_token_is_private():
    service = create_execution_service(now=NOW, auto_ack=True)
    created = await service.create_round(create_command())
    await service.send_round(
        SendRFQRoundCommand(
            context=context("idem_send"),
            rfq_round_id=created.rfq_round_id,
            expected_round_version=created.round_version,
            channel="manual_link",
        )
    )
    messages = service.delivery_gateway.messages
    alpha_token = messages[0].response_token
    context_dto = service.get_response_context(alpha_token)

    assert context_dto.supplier_id == "supplier_alpha"
    assert "supplier_beta" not in context_dto.model_dump_json()

    invalid = quote(420_000, "Alpha")
    invalid.total_cents += 1
    with pytest.raises(DomainError, match="QUOTE_TOTAL_MISMATCH"):
        await service.submit_quote(alpha_token, invalid)

    accepted = await service.submit_quote(alpha_token, quote(420_000, "Alpha"))
    assert accepted.total_cents == 420_000
    assert accepted.price_per_person_cents == 5_250
    assert accepted.status == "FINAL"


@pytest.mark.asyncio
async def test_comparison_is_deterministic_and_excludes_ineligible_quotes():
    service = create_execution_service(now=NOW, auto_ack=True)
    created = await service.create_round(create_command())
    await service.send_round(
        SendRFQRoundCommand(
            context=context("idem_send"),
            rfq_round_id=created.rfq_round_id,
            expected_round_version=created.round_version,
            channel="manual_link",
        )
    )
    alpha_message, beta_message = service.delivery_gateway.messages
    await service.submit_quote(alpha_message.response_token, quote(420_000, "Alpha"))
    await service.submit_quote(beta_message.response_token, quote(435_000, "Beta"))
    quote_status = await service.get_quote_status(created.rfq_round_id)
    command = CompareQuotesCommand(
        context=context("idem_compare"),
        procurement_request_id="pr_demo_coffee_break",
        rfq_round_id=created.rfq_round_id,
        expected_quote_collection_version=quote_status.collection_version,
    )

    first = await service.compare(command)
    replay = await service.compare(command)

    assert first.recommended_quote is not None
    assert first.candidates[0].supplier_id == "supplier_alpha"
    assert first.candidates[0].score_basis_points == sum(
        component.points_basis_points for component in first.candidates[0].score_components
    )
    assert replay.idempotent_replay is True


@pytest.mark.asyncio
async def test_agent_cannot_approve_and_award_binds_quote_version():
    service, comparison = await ready_comparison()
    selected = comparison.recommended_quote
    assert selected is not None
    approval = await service.request_approval(
        RequestApprovalCommand(
            context=context("idem_approval"),
            procurement_request_id="pr_demo_coffee_break",
            comparison_id=comparison.comparison_id,
            comparison_version=comparison.comparison_version,
            selected_quote=selected,
            approver_user_id="buyer_gabriel",
        )
    )

    with pytest.raises(DomainError, match="POLICY_DENIED"):
        await service.decide_approval(
            approval.approval_id,
            actor_type="agent",
            actor_id="agent_demo",
            approve=True,
            idempotency_key="idem_decision_agent",
        )

    approved = await service.decide_approval(
        approval.approval_id,
        actor_type="human",
        actor_id="buyer_gabriel",
        approve=True,
        idempotency_key="idem_decision_human",
    )
    award = await service.send_award(
        SendAwardCommand(
            context=context("idem_award"),
            procurement_request_id="pr_demo_coffee_break",
            approval_id=approved.approval_id,
            expected_approval_version=approved.approval_version,
        )
    )

    assert award.approved_quote == selected
    assert award.approved_total_cents == 420_000


@pytest.mark.asyncio
async def test_full_procurement_happy_path_reaches_ready_for_contracting():
    service, comparison = await ready_comparison(auto_ack=True)
    selected = comparison.recommended_quote
    assert selected is not None
    approval = await service.request_approval(
        RequestApprovalCommand(
            context=context("idem_approval_e2e"),
            procurement_request_id="pr_demo_coffee_break",
            comparison_id=comparison.comparison_id,
            comparison_version=comparison.comparison_version,
            selected_quote=selected,
            approver_user_id="buyer_gabriel",
        )
    )
    approved = await service.decide_approval(
        approval.approval_id,
        actor_type="human",
        actor_id="buyer_gabriel",
        approve=True,
        idempotency_key="idem_decision_e2e",
    )
    award = await service.send_award(
        SendAwardCommand(
            context=context("idem_award_e2e"),
            procurement_request_id="pr_demo_coffee_break",
            approval_id=approved.approval_id,
            expected_approval_version=approved.approval_version,
        )
    )
    assert award.status == "DELIVERED"

    award_message = service.delivery_gateway.messages[-1]
    accepted = await service.accept_award(
        award_message.response_token,
        respondent_name="Alpha",
        idempotency_key="idem_accept_e2e",
    )
    assert accepted.ready_for_contracting is False

    completed = await service.confirm_reservation(
        accepted.award_id,
        event_date="2026-08-22",
        delivery_window="08:30",
        people_count=80,
        confirmed_by="Alpha",
        idempotency_key="idem_reservation_e2e",
    )
    replay = await service.confirm_reservation(
        accepted.award_id,
        event_date="2026-08-22",
        delivery_window="08:30",
        people_count=80,
        confirmed_by="Alpha",
        idempotency_key="idem_reservation_e2e",
    )

    assert completed.ready_for_contracting is True
    assert completed.reservation_status == "CONFIRMED"
    assert replay.idempotent_replay is True
    assert service.get_procurement_status("pr_demo_coffee_break") == "READY_FOR_CONTRACTING"
    assert service.audit_events[-1].event_type == "PROCUREMENT_READY_FOR_CONTRACTING"


async def ready_comparison(*, auto_ack: bool = True):
    service = create_execution_service(now=NOW, auto_ack=auto_ack)
    created = await service.create_round(create_command())
    await service.send_round(
        SendRFQRoundCommand(
            context=context("idem_send_ready"),
            rfq_round_id=created.rfq_round_id,
            expected_round_version=created.round_version,
            channel="manual_link",
        )
    )
    alpha_message, beta_message = service.delivery_gateway.messages
    await service.submit_quote(alpha_message.response_token, quote(420_000, "Alpha"))
    await service.submit_quote(beta_message.response_token, quote(435_000, "Beta"))
    status = await service.get_quote_status(created.rfq_round_id)
    comparison = await service.compare(
        CompareQuotesCommand(
            context=context("idem_compare_ready"),
            procurement_request_id="pr_demo_coffee_break",
            rfq_round_id=created.rfq_round_id,
            expected_quote_collection_version=status.collection_version,
        )
    )
    return service, comparison
