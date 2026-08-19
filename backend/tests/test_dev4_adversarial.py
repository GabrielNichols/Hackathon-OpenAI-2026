from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest

from app.modules.messaging.gateway import FakeDeliveryGateway
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
from app.modules.rfq.service import ProcurementExecutionService
from app.modules.rfq.store import InMemoryExecutionStore
from app.shared.errors import DomainError, ErrorCode
from app.shared.tokens import SignedTokenService

NOW = datetime(2026, 8, 19, 15, 0, tzinfo=UTC)
REQUEST_ID = "pr_adversarial"
APPROVER_ID = "buyer_gabriel"


@dataclass
class MutableClock:
    value: datetime

    def now(self) -> datetime:
        return self.value

    def advance(self, delta: timedelta) -> None:
        self.value += delta


def build_service(*, auto_ack: bool) -> tuple[ProcurementExecutionService, MutableClock]:
    clock = MutableClock(NOW)
    service = ProcurementExecutionService(
        store=InMemoryExecutionStore(),
        clock=clock,
        token_service=SignedTokenService("adversarial-secret", clock=clock),
        delivery_gateway=FakeDeliveryGateway(auto_ack=auto_ack, clock=clock),
    )
    return service, clock


def context(key: str) -> CommandContextDTO:
    return CommandContextDTO(
        tenant_id="org_adversarial",
        idempotency_key=key,
        correlation_id="cor_adversarial",
        actor_type="agent",
        actor_id="agent_adversarial",
        agent_run_id="run_adversarial",
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
        approver_user_id=APPROVER_ID,
    )


def create_command(clock: MutableClock, *, key: str = "adv:create") -> CreateRFQRoundCommand:
    return CreateRFQRoundCommand(
        context=context(key),
        procurement_request_id=REQUEST_ID,
        request_version=1,
        plan_version=1,
        recipient_supplier_ids=["supplier_alpha", "supplier_beta"],
        response_deadline=clock.now() + timedelta(hours=3),
        requirements=requirements(),
        execution_policy=policy(),
    )


def quote(
    clock: MutableClock,
    total_cents: int,
    supplier_name: str,
    *,
    validity: timedelta = timedelta(hours=2),
) -> QuoteSubmissionDTO:
    delivery_fee_cents = 20_000
    return QuoteSubmissionDTO(
        availability_confirmed=True,
        subtotal_cents=total_cents - delivery_fee_cents,
        delivery_fee_cents=delivery_fee_cents,
        other_fee_cents=0,
        total_cents=total_cents,
        included_items=["cafe", "salgados", "frutas"],
        substitutions=[],
        invoice_available=True,
        vegetarian_status="confirmed",
        vegan_status="confirmed",
        gluten_free_status="confirmed",
        cross_contamination_warning="producao separada sem certificacao",
        no_single_use_plastic_confirmed=True,
        valid_until=clock.now() + validity,
        cancellation_terms="Cancelamento sem custo ate 24h antes",
        respondent_name=supplier_name,
        respondent_contact=f"{supplier_name.lower()}@example.test",
        supplier_confirmation=True,
        sustainability_score=5,
        history_score=4,
        response_time_minutes=10,
    )


async def create_and_send(
    service: ProcurementExecutionService,
    clock: MutableClock,
    *,
    send_key: str = "adv:send",
):
    created = await service.create_round(create_command(clock))
    delivery = await service.send_round(
        SendRFQRoundCommand(
            context=context(send_key),
            rfq_round_id=created.rfq_round_id,
            expected_round_version=created.round_version,
            channel="manual_link",
        )
    )
    return created, delivery


async def ready_comparison(
    service: ProcurementExecutionService,
    clock: MutableClock,
    *,
    validity: timedelta = timedelta(hours=2),
):
    created, _delivery = await create_and_send(service, clock)
    alpha_message, beta_message = service.delivery_gateway.messages
    await service.submit_quote(
        alpha_message.response_token,
        quote(clock, 420_000, "Alpha", validity=validity),
    )
    await service.submit_quote(
        beta_message.response_token,
        quote(clock, 435_000, "Beta", validity=validity),
    )
    status = await service.get_quote_status(created.rfq_round_id)
    comparison = await service.compare(
        CompareQuotesCommand(
            context=context("adv:compare"),
            procurement_request_id=REQUEST_ID,
            rfq_round_id=created.rfq_round_id,
            expected_quote_collection_version=status.collection_version,
        )
    )
    return created, comparison


async def approved_award(
    service: ProcurementExecutionService,
    clock: MutableClock,
):
    _created, comparison = await ready_comparison(service, clock)
    assert comparison.recommended_quote is not None
    requested = await service.request_approval(
        RequestApprovalCommand(
            context=context("adv:approval:request"),
            procurement_request_id=REQUEST_ID,
            comparison_id=comparison.comparison_id,
            comparison_version=comparison.comparison_version,
            selected_quote=comparison.recommended_quote,
            approver_user_id=APPROVER_ID,
        )
    )
    approved = await service.decide_approval(
        requested.approval_id,
        actor_type="human",
        actor_id=APPROVER_ID,
        approve=True,
        idempotency_key="adv:approval:grant",
    )
    award = await service.send_award(
        SendAwardCommand(
            context=context("adv:award:first"),
            procurement_request_id=REQUEST_ID,
            approval_id=approved.approval_id,
            expected_approval_version=approved.approval_version,
        )
    )
    return comparison, approved, award


@pytest.mark.asyncio
async def test_reading_draft_delivery_status_does_not_mutate_round_or_version():
    service, clock = build_service(auto_ack=False)
    created = await service.create_round(create_command(clock))
    events_before = list(service.audit_events)

    status = await service.get_delivery_status(created.rfq_round_id)

    stored = service.store.rounds[created.rfq_round_id]["dto"]
    assert status.round_version == created.round_version == 1
    assert stored.status == "DRAFT"
    assert stored.round_version == 1
    assert service.audit_events == events_before


@pytest.mark.asyncio
async def test_delivery_audit_records_command_and_system_transition_contexts():
    service, clock = build_service(auto_ack=False)
    created, delivery = await create_and_send(
        service,
        clock,
        send_key="adv:send:audit",
    )

    dispatch = next(
        event for event in service.audit_events if event.event_type == "RFQ_DISPATCH_STARTED"
    )
    assert dispatch.previous_state == "DRAFT"
    assert dispatch.new_state == "DISPATCHING"
    assert dispatch.origin == "delivery_gateway"
    assert dispatch.agent_run_id == "run_adversarial"
    assert dispatch.idempotency_key == "adv:send:audit"

    first_message = service.delivery_gateway.messages[0]
    service.delivery_gateway.ack(first_message.external_id)
    await service.get_delivery_status(created.rfq_round_id)

    delivered = next(
        event
        for event in service.audit_events
        if event.event_type == "RFQ_DELIVERY_CONFIRMED"
        and event.aggregate_id == delivery.deliveries[0].recipient_id
    )
    assert delivered.previous_state == "SENT_TO_GATEWAY"
    assert delivered.new_state == "DELIVERED"
    assert delivered.origin == "delivery_gateway"
    assert delivered.actor_type == "system"
    assert delivered.agent_run_id is None

    activated = next(
        event for event in service.audit_events if event.event_type == "RFQ_ROUND_ACTIVATED"
    )
    assert activated.previous_state == "DISPATCHING"
    assert activated.new_state == "ACTIVE"
    assert activated.origin == "delivery_gateway"
    assert all(event.origin for event in service.audit_events)


@pytest.mark.asyncio
async def test_second_send_with_new_command_key_does_not_duplicate_gateway_messages():
    service, clock = build_service(auto_ack=True)
    created, first = await create_and_send(service, clock, send_key="adv:send:first")
    first_external_ids = [message.external_id for message in service.delivery_gateway.messages]

    second = await service.send_round(
        SendRFQRoundCommand(
            context=context("adv:send:second"),
            rfq_round_id=created.rfq_round_id,
            expected_round_version=first.round_version,
            channel="manual_link",
        )
    )

    assert second.confirmed_count == 2
    assert len(service.delivery_gateway.messages) == 2
    assert [
        message.external_id for message in service.delivery_gateway.messages
    ] == first_external_ids
    assert sum(event.event_type == "RFQ_DELIVERY_CONFIRMED" for event in service.audit_events) == 2


@pytest.mark.asyncio
async def test_quote_submission_before_delivery_ack_is_blocked_without_side_effects():
    service, clock = build_service(auto_ack=False)
    await create_and_send(service, clock)
    response_token = service.delivery_gateway.messages[0].response_token

    with pytest.raises(DomainError) as captured:
        await service.submit_quote(response_token, quote(clock, 420_000, "Alpha"))

    assert captured.value.code == ErrorCode.INVALID_STATE
    assert service.store.quotes == {}
    assert not any(event.event_type == "QUOTE_SUBMITTED" for event in service.audit_events)


@pytest.mark.asyncio
async def test_crossed_request_ids_and_out_of_policy_approver_are_blocked():
    service, clock = build_service(auto_ack=True)
    created, _delivery = await create_and_send(service, clock)
    alpha_message, beta_message = service.delivery_gateway.messages
    await service.submit_quote(alpha_message.response_token, quote(clock, 420_000, "Alpha"))
    await service.submit_quote(beta_message.response_token, quote(clock, 435_000, "Beta"))
    quote_status = await service.get_quote_status(created.rfq_round_id)

    with pytest.raises(DomainError) as crossed_compare:
        await service.compare(
            CompareQuotesCommand(
                context=context("adv:compare:crossed"),
                procurement_request_id="pr_other",
                rfq_round_id=created.rfq_round_id,
                expected_quote_collection_version=quote_status.collection_version,
            )
        )
    assert crossed_compare.value.code == ErrorCode.VALIDATION_ERROR

    comparison = await service.compare(
        CompareQuotesCommand(
            context=context("adv:compare:valid"),
            procurement_request_id=REQUEST_ID,
            rfq_round_id=created.rfq_round_id,
            expected_quote_collection_version=quote_status.collection_version,
        )
    )
    assert comparison.recommended_quote is not None

    with pytest.raises(DomainError) as crossed_approval:
        await service.request_approval(
            RequestApprovalCommand(
                context=context("adv:approval:crossed"),
                procurement_request_id="pr_other",
                comparison_id=comparison.comparison_id,
                comparison_version=comparison.comparison_version,
                selected_quote=comparison.recommended_quote,
                approver_user_id=APPROVER_ID,
            )
        )
    assert crossed_approval.value.code == ErrorCode.VALIDATION_ERROR

    with pytest.raises(DomainError) as bad_approver:
        await service.request_approval(
            RequestApprovalCommand(
                context=context("adv:approval:bad-approver"),
                procurement_request_id=REQUEST_ID,
                comparison_id=comparison.comparison_id,
                comparison_version=comparison.comparison_version,
                selected_quote=comparison.recommended_quote,
                approver_user_id="buyer_outside_policy",
            )
        )
    assert bad_approver.value.code == ErrorCode.POLICY_DENIED
    assert service.store.approvals == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("expiration_stage", ["approval", "award"])
async def test_expired_quote_cannot_be_approved_or_awarded(expiration_stage: str):
    service, clock = build_service(auto_ack=True)
    _created, comparison = await ready_comparison(
        service,
        clock,
        validity=timedelta(minutes=30),
    )
    assert comparison.recommended_quote is not None
    requested = await service.request_approval(
        RequestApprovalCommand(
            context=context(f"adv:expiry:{expiration_stage}:request"),
            procurement_request_id=REQUEST_ID,
            comparison_id=comparison.comparison_id,
            comparison_version=comparison.comparison_version,
            selected_quote=comparison.recommended_quote,
            approver_user_id=APPROVER_ID,
        )
    )

    if expiration_stage == "approval":
        clock.advance(timedelta(minutes=31))
        with pytest.raises(DomainError) as captured:
            await service.decide_approval(
                requested.approval_id,
                actor_type="human",
                actor_id=APPROVER_ID,
                approve=True,
                idempotency_key="adv:expiry:approval:grant",
            )
    else:
        approved = await service.decide_approval(
            requested.approval_id,
            actor_type="human",
            actor_id=APPROVER_ID,
            approve=True,
            idempotency_key="adv:expiry:award:grant",
        )
        clock.advance(timedelta(minutes=31))
        with pytest.raises(DomainError) as captured:
            await service.send_award(
                SendAwardCommand(
                    context=context("adv:expiry:award:send"),
                    procurement_request_id=REQUEST_ID,
                    approval_id=approved.approval_id,
                    expected_approval_version=approved.approval_version,
                )
            )

    assert captured.value.code == ErrorCode.QUOTE_EXPIRED
    assert service.store.awards == {}


@pytest.mark.asyncio
async def test_award_and_reservation_are_unique_by_business_identity_not_only_key():
    service, clock = build_service(auto_ack=True)
    _comparison, approved, first_award = await approved_award(service, clock)
    gateway_count_after_first_award = len(service.delivery_gateway.messages)

    award_replay = await service.send_award(
        SendAwardCommand(
            context=context("adv:award:new-command-key"),
            procurement_request_id=REQUEST_ID,
            approval_id=approved.approval_id,
            expected_approval_version=approved.approval_version,
        )
    )

    assert award_replay.award_id == first_award.award_id
    assert award_replay.idempotent_replay is True
    assert len(service.store.awards) == 1
    assert len(service.delivery_gateway.messages) == gateway_count_after_first_award

    accepted = await service.accept_award(
        service.delivery_gateway.messages[-1].response_token,
        respondent_name="Alpha",
        terms_snapshot_hash=first_award.terms_snapshot_hash,
        terms_accepted=True,
        idempotency_key="adv:accept",
    )
    first_reservation = await service.confirm_reservation(
        accepted.award_id,
        event_date="2026-08-22",
        delivery_window="08:30",
        people_count=80,
        confirmed_by="Alpha",
        idempotency_key="adv:reservation:first",
    )
    reservation_replay = await service.confirm_reservation(
        accepted.award_id,
        event_date="2026-08-22",
        delivery_window="08:30",
        people_count=80,
        confirmed_by="Alpha",
        idempotency_key="adv:reservation:new-command-key",
    )

    assert first_reservation.ready_for_contracting is True
    assert reservation_replay.idempotent_replay is True
    assert len(service.store.reservations) == 1
    assert sum(event.event_type == "AWARD_CREATED" for event in service.audit_events) == 1
    assert sum(event.event_type == "CAPACITY_RESERVED" for event in service.audit_events) == 1


@pytest.mark.asyncio
async def test_reservation_rejects_different_actor_and_frozen_terms():
    service, clock = build_service(auto_ack=True)
    _comparison, _approved, award = await approved_award(service, clock)
    accepted = await service.accept_award(
        service.delivery_gateway.messages[-1].response_token,
        respondent_name="Alpha",
        terms_snapshot_hash=award.terms_snapshot_hash,
        terms_accepted=True,
        idempotency_key="adv:accept:terms",
    )

    with pytest.raises(DomainError) as wrong_actor:
        await service.confirm_reservation(
            accepted.award_id,
            event_date="2026-08-22",
            delivery_window="08:30",
            people_count=80,
            confirmed_by="Mallory",
            idempotency_key="adv:reservation:wrong-actor",
        )
    assert wrong_actor.value.code == ErrorCode.POLICY_DENIED

    divergent_terms = [
        {"event_date": "2026-08-23", "delivery_window": "08:30", "people_count": 80},
        {"event_date": "2026-08-22", "delivery_window": "09:00", "people_count": 80},
        {"event_date": "2026-08-22", "delivery_window": "08:30", "people_count": 81},
    ]
    for index, terms in enumerate(divergent_terms):
        with pytest.raises(DomainError) as mismatch:
            await service.confirm_reservation(
                award.award_id,
                **terms,
                confirmed_by="Alpha",
                idempotency_key=f"adv:reservation:mismatch:{index}",
            )
        assert mismatch.value.code == ErrorCode.VALIDATION_ERROR

    assert service.store.reservations == {}


@pytest.mark.asyncio
async def test_response_tokens_reject_wrong_purpose_and_tampering():
    service, clock = build_service(auto_ack=True)
    await create_and_send(service, clock)
    rfq_token = service.delivery_gateway.messages[0].response_token

    with pytest.raises(DomainError) as wrong_purpose:
        await service.accept_award(
            rfq_token,
            respondent_name="Alpha",
            terms_snapshot_hash="not-used",
            terms_accepted=True,
            idempotency_key="adv:token:wrong-purpose",
        )
    assert wrong_purpose.value.code == ErrorCode.INVALID_RESPONSE_TOKEN

    payload, signature = rfq_token.split(".")
    replacement = "A" if payload[0] != "A" else "B"
    tampered_token = f"{replacement}{payload[1:]}.{signature}"
    with pytest.raises(DomainError) as tampered:
        service.get_response_context(tampered_token)
    assert tampered.value.code == ErrorCode.INVALID_RESPONSE_TOKEN

    recipient_id = service.delivery_gateway.messages[0].recipient_id
    round_id = service.delivery_gateway.messages[0].metadata["rfq_round_id"]
    wrong_tenant_token = service.token_service.issue(
        "rfq_response",
        recipient_id,
        expires_at=service.store.rounds[round_id]["dto"].response_deadline,
        metadata={
            "rfq_round_id": round_id,
            "supplier_id": "supplier_alpha",
            "tenant_id": "org_other",
        },
    )
    with pytest.raises(DomainError) as wrong_tenant:
        service.get_response_context(wrong_tenant_token)
    assert wrong_tenant.value.code == ErrorCode.INVALID_RESPONSE_TOKEN
    assert service.store.quotes == {}


@pytest.mark.asyncio
async def test_two_valid_quotes_and_explicit_sustainability_are_required():
    service, clock = build_service(auto_ack=True)
    created, _delivery = await create_and_send(service, clock)
    alpha_message, beta_message = service.delivery_gateway.messages

    missing_packaging_confirmation = quote(clock, 420_000, "Alpha")
    missing_packaging_confirmation.no_single_use_plastic_confirmed = False
    alpha_v1 = await service.submit_quote(
        alpha_message.response_token,
        missing_packaging_confirmation,
        idempotency_key="adv:quote:alpha:v1",
    )
    await service.submit_quote(
        beta_message.response_token,
        quote(clock, 435_000, "Beta"),
        idempotency_key="adv:quote:beta:v1",
    )

    status = await service.get_quote_status(created.rfq_round_id)
    assert alpha_v1.status == "NEEDS_CLARIFICATION"
    assert "NO_SINGLE_USE_PLASTIC_REQUIREMENT_NOT_MET" in alpha_v1.validation_errors
    assert status.valid_count == 1
    assert status.ready_for_comparison is False

    with pytest.raises(DomainError) as insufficient:
        await service.compare(
            CompareQuotesCommand(
                context=context("adv:compare:insufficient"),
                procurement_request_id=REQUEST_ID,
                rfq_round_id=created.rfq_round_id,
                expected_quote_collection_version=status.collection_version,
            )
        )
    assert insufficient.value.code == ErrorCode.INVALID_STATE

    alpha_v2 = await service.submit_quote(
        alpha_message.response_token,
        quote(clock, 420_000, "Alpha"),
        idempotency_key="adv:quote:alpha:v2",
    )
    refreshed = await service.get_quote_status(created.rfq_round_id)
    assert alpha_v2.quote_version == 2
    assert alpha_v2.status == "FINAL"
    assert refreshed.valid_count == 2
    assert refreshed.ready_for_comparison is True
    assert any(
        event.event_type == "CLARIFICATION_ANSWERED"
        for event in service.audit_events
    )


@pytest.mark.asyncio
async def test_quote_post_idempotency_conflict_and_approval_invalidation():
    service, clock = build_service(auto_ack=True)
    created, _delivery = await create_and_send(service, clock)
    alpha_message, beta_message = service.delivery_gateway.messages
    alpha_v1 = await service.submit_quote(
        alpha_message.response_token,
        quote(clock, 420_000, "Alpha"),
        idempotency_key="adv:quote:alpha:stable-key",
    )
    await service.submit_quote(
        beta_message.response_token,
        quote(clock, 435_000, "Beta"),
        idempotency_key="adv:quote:beta:stable-key",
    )

    with pytest.raises(DomainError) as conflicting_retry:
        await service.submit_quote(
            alpha_message.response_token,
            quote(clock, 410_000, "Alpha"),
            idempotency_key="adv:quote:alpha:stable-key",
        )
    assert conflicting_retry.value.code == ErrorCode.IDEMPOTENCY_CONFLICT

    status = await service.get_quote_status(created.rfq_round_id)
    comparison = await service.compare(
        CompareQuotesCommand(
            context=context("adv:compare:before-update"),
            procurement_request_id=REQUEST_ID,
            rfq_round_id=created.rfq_round_id,
            expected_quote_collection_version=status.collection_version,
        )
    )
    assert comparison.recommended_quote is not None
    approval = await service.request_approval(
        RequestApprovalCommand(
            context=context("adv:approval:before-update"),
            procurement_request_id=REQUEST_ID,
            comparison_id=comparison.comparison_id,
            comparison_version=comparison.comparison_version,
            selected_quote=comparison.recommended_quote,
            approver_user_id=APPROVER_ID,
        )
    )
    assert comparison.recommended_quote.quote_id == alpha_v1.quote_id

    await service.submit_quote(
        alpha_message.response_token,
        quote(clock, 410_000, "Alpha"),
        idempotency_key="adv:quote:alpha:new-version",
    )
    invalidated = await service.get_approval_status(approval.approval_id)
    assert invalidated.status == "INVALIDATED"
    assert any(
        event.event_type == "APPROVAL_INVALIDATED"
        for event in service.audit_events
    )


@pytest.mark.asyncio
async def test_award_acceptance_binds_displayed_terms_and_decline_is_real():
    service, clock = build_service(auto_ack=True)
    _comparison, _approved, award = await approved_award(service, clock)
    award_token = service.delivery_gateway.messages[-1].response_token

    with pytest.raises(DomainError) as stale_terms:
        await service.accept_award(
            award_token,
            respondent_name="Alpha",
            terms_snapshot_hash="0" * 64,
            terms_accepted=True,
            idempotency_key="adv:accept:wrong-terms",
        )
    assert stale_terms.value.code == ErrorCode.STALE_VERSION

    declined = await service.decline_award(
        award_token,
        respondent_name="Alpha",
        reason="Capacidade indisponível",
        idempotency_key="adv:award:decline",
    )
    assert declined.award_id == award.award_id
    assert declined.status == "DECLINED"
    assert service.get_procurement_status(REQUEST_ID) == "AWARD_DECLINED"
