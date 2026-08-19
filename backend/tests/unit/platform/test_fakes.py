from datetime import UTC, date, datetime, timedelta

import pytest

from app.contracts import (
    ActorType,
    ApprovalStatus,
    AwardStatus,
    CreateRFQRoundCommand,
    ErrorCode,
    NegotiationCommand,
    QuoteDecisionPort,
    RequestApprovalCommand,
    SendAwardCommand,
    SendRFQRoundCommand,
    SupplierCandidateDTO,
    SupplierSearchCriteria,
    SupplierState,
)
from app.platform.clock import FixedClock
from app.platform.fakes import (
    FakeRFQExecutionPort,
    FakeSupplierDirectory,
    StaticQuoteComparisonPort,
    StaticQuoteDecisionPort,
)
from app.platform.idempotency import IdempotencyConflictError
from app.platform.ids import SequenceIdGenerator
from app.testing import assert_core_port, make_quote_comparison

NOW = datetime(2026, 8, 19, 15, tzinfo=UTC)


def candidate(status: SupplierState, *, invoice: bool | None = True) -> SupplierCandidateDTO:
    return SupplierCandidateDTO(
        supplier_id="sup_alpha" if status is SupplierState.ACTIVE else "sup_draft",
        display_name="Supplier Alpha",
        status=status,
        categories=["corporate_catering"],
        service_areas=["Sao Paulo"],
        minimum_people=20,
        maximum_people=200,
        lead_time_hours=24,
        invoice_available=invoice,
        dietary_capabilities={"vegan": "supported"},
        sustainability_tags=["no_single_use_plastic"],
        last_confirmed_at=NOW,
        evidence_refs=["evd_alpha"],
        missing_fields=[],
    )


def rfq_command(
    *,
    idempotency_key: str = "rfq-create-1",
    people_count: int = 80,
) -> CreateRFQRoundCommand:
    return CreateRFQRoundCommand(
        procurement_request_id="pr_demo",
        request_version=1,
        recipient_supplier_ids=["sup_alpha"],
        response_deadline=NOW + timedelta(days=1),
        requirements_snapshot={"people_count": people_count},
        policy_snapshot={"maximum_total_cents": 450_000},
        idempotency_key=idempotency_key,
    )


@pytest.mark.asyncio
async def test_supplier_directory_returns_only_active_confirmed_suppliers() -> None:
    directory = FakeSupplierDirectory(
        [candidate(SupplierState.ACTIVE), candidate(SupplierState.DRAFT)]
    )
    result = await directory.search(
        SupplierSearchCriteria(
            tenant_id="org_demo",
            category="corporate_catering",
            city="Sao Paulo",
            district=None,
            event_date=date(2026, 8, 28),
            delivery_time=None,
            people_count=80,
            invoice_required=True,
            dietary_requirements={"vegan": 4},
            mandatory_tags=["no_single_use_plastic"],
            maximum_lead_time_hours=48,
        )
    )
    assert [item.supplier_id for item in result] == ["sup_alpha"]


@pytest.mark.asyncio
async def test_fake_rfq_round_records_real_delivery_ack() -> None:
    port = FakeRFQExecutionPort(FixedClock(NOW), SequenceIdGenerator())
    round_item = await port.create_round(rfq_command())
    batch = await port.send_round(
        SendRFQRoundCommand(
            rfq_round_id=round_item.rfq_round_id,
            channel="email",
            idempotency_key="rfq-send-1",
        )
    )
    assert batch.all_confirmed
    assert batch.deliveries[0].external_id == "delivery_1"
    assert (await port.get_status(round_item.rfq_round_id)).version == 1


@pytest.mark.asyncio
async def test_fake_rfq_replays_create_and_send_without_duplicate_effects() -> None:
    port = FakeRFQExecutionPort(FixedClock(NOW), SequenceIdGenerator())
    command = rfq_command()
    first_round = await port.create_round(command)
    replayed_round = await port.create_round(command)
    assert replayed_round == first_round

    send = SendRFQRoundCommand(
        rfq_round_id=first_round.rfq_round_id,
        channel="email",
        idempotency_key="rfq-send-1",
    )
    first_batch = await port.send_round(send)
    replayed_batch = await port.send_round(send)
    assert replayed_batch == first_batch
    assert (await port.get_status(first_round.rfq_round_id)).version == 1

    next_round = await port.create_round(rfq_command(idempotency_key="rfq-create-2"))
    assert next_round.rfq_round_id == "rfq_2"


@pytest.mark.asyncio
async def test_fake_rfq_rejects_same_key_with_different_payload() -> None:
    port = FakeRFQExecutionPort(FixedClock(NOW), SequenceIdGenerator())
    round_item = await port.create_round(rfq_command())

    with pytest.raises(IdempotencyConflictError) as create_error:
        await port.create_round(rfq_command(people_count=81))
    assert create_error.value.code is ErrorCode.IDEMPOTENCY_CONFLICT

    await port.send_round(
        SendRFQRoundCommand(
            rfq_round_id=round_item.rfq_round_id,
            channel="email",
            idempotency_key="rfq-send-1",
        )
    )
    with pytest.raises(IdempotencyConflictError) as send_error:
        await port.send_round(
            SendRFQRoundCommand(
                rfq_round_id=round_item.rfq_round_id,
                channel="manual_link",
                idempotency_key="rfq-send-1",
            )
        )
    assert send_error.value.code is ErrorCode.IDEMPOTENCY_CONFLICT


@pytest.mark.asyncio
async def test_static_quote_decision_fake_implements_complete_port() -> None:
    comparison = make_quote_comparison()
    port = StaticQuoteDecisionPort(comparison)
    assert StaticQuoteComparisonPort is StaticQuoteDecisionPort
    assert_core_port(port, QuoteDecisionPort)
    assert await port.compare(comparison.procurement_request_id) == comparison

    selected = comparison.entries[0]
    negotiation = await port.run_negotiation(
        NegotiationCommand(
            procurement_request_id=comparison.procurement_request_id,
            quote_id=selected.quote_id,
            quote_version=selected.quote_version,
            topic="total_price",
            requested_change={"target_total_cents": 400_000},
            idempotency_key="negotiation-1",
        )
    )
    assert negotiation.status == "NO_CHANGE"

    approval = await port.request_approval(
        RequestApprovalCommand(
            procurement_request_id=comparison.procurement_request_id,
            comparison_id=comparison.comparison_id,
            quote_id=selected.quote_id,
            quote_version=selected.quote_version,
            approver_user_id="approver_demo",
            requested_by_actor_type=ActorType.AGENT,
            requested_by_actor_id="agent_demo",
            idempotency_key="approval-1",
        )
    )
    assert approval.status is ApprovalStatus.REQUESTED

    award = await port.send_award(
        SendAwardCommand(
            procurement_request_id=comparison.procurement_request_id,
            approval_id=approval.approval_id,
            supplier_id=selected.supplier_id,
            approved_quote_id=selected.quote_id,
            approved_quote_version=selected.quote_version,
            idempotency_key="award-1",
        )
    )
    assert award.status is AwardStatus.CREATED
    assert await port.get_award_status(award.award_id) == award
