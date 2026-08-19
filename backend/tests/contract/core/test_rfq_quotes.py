from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.contracts import (
    ActorType,
    ApprovalDTO,
    ApprovalStatus,
    AwardDTO,
    AwardStatus,
    CreateRFQRoundCommand,
    DeliveryBatchDTO,
    DeliveryDTO,
    QuoteComparisonDTO,
    QuoteComparisonEntryDTO,
    RequestApprovalCommand,
    RFQDeliveryStatus,
    SendAwardCommand,
    SendRFQRoundCommand,
)

FIXED_NOW = datetime(2026, 8, 19, 15, 0, tzinfo=UTC)


def test_create_rfq_round_command_freezes_request_and_policy_snapshots() -> None:
    command = CreateRFQRoundCommand(
        procurement_request_id="pr_demo",
        request_version=3,
        recipient_supplier_ids=["sup_alpha", "sup_beta"],
        response_deadline=datetime(2026, 8, 20, 18, 0, tzinfo=UTC),
        requirements_snapshot={"people_count": 80, "invoice_required": True},
        policy_snapshot={"maximum_total_cents": 450_000},
        idempotency_key="idem-rfq-round-001",
    )

    assert CreateRFQRoundCommand.model_validate_json(command.model_dump_json()) == command
    assert command.request_version == 3
    assert command.policy_snapshot["maximum_total_cents"] == 450_000


def test_send_rfq_round_rejects_unknown_channel() -> None:
    with pytest.raises(ValidationError):
        SendRFQRoundCommand(
            rfq_round_id="rfq_001",
            channel="whatsapp",
            idempotency_key="idem-send-rfq-001",
        )


def test_delivered_item_requires_gateway_ack_and_timestamp() -> None:
    base = {
        "recipient_id": "rcp_001",
        "supplier_id": "sup_alpha",
        "status": "DELIVERED",
        "error_code": None,
    }
    with pytest.raises(ValidationError):
        DeliveryDTO.model_validate({**base, "external_id": None, "delivered_at": FIXED_NOW})
    with pytest.raises(ValidationError):
        DeliveryDTO.model_validate({**base, "external_id": "gateway_001", "delivered_at": None})


def test_delivery_batch_preserves_partial_delivery() -> None:
    delivered = DeliveryDTO(
        recipient_id="rcp_001",
        supplier_id="sup_alpha",
        status=RFQDeliveryStatus.DELIVERED,
        external_id="gateway_001",
        delivered_at=FIXED_NOW,
    )
    pending = DeliveryDTO(
        recipient_id="rcp_002",
        supplier_id="sup_beta",
        status=RFQDeliveryStatus.QUEUED,
    )
    batch = DeliveryBatchDTO(
        rfq_round_id="rfq_001",
        deliveries=[delivered, pending],
        all_confirmed=False,
    )

    assert batch.all_confirmed is False
    assert [delivery.status for delivery in batch.deliveries] == [
        RFQDeliveryStatus.DELIVERED,
        RFQDeliveryStatus.QUEUED,
    ]


def test_delivery_batch_rejects_false_all_confirmed_claim() -> None:
    pending = DeliveryDTO(
        recipient_id="rcp_002",
        supplier_id="sup_beta",
        status=RFQDeliveryStatus.QUEUED,
    )
    with pytest.raises(ValidationError):
        DeliveryBatchDTO(rfq_round_id="rfq_001", deliveries=[pending], all_confirmed=True)


def _comparison_entry(**overrides: object) -> QuoteComparisonEntryDTO:
    values: dict[str, object] = {
        "quote_id": "quo_alpha_v1",
        "quote_version": 1,
        "supplier_id": "sup_alpha",
        "eligible": True,
        "total_cents": 420_000,
        "currency": "BRL",
        "score": 90,
        "rank": 1,
        "reason_codes": [],
        "evidence_refs": ["evi_quote_alpha"],
    }
    values.update(overrides)
    return QuoteComparisonEntryDTO.model_validate(values)


@pytest.mark.parametrize("field,value", [("total_cents", 4200.5), ("score", 90.5)])
def test_quote_comparison_entry_rejects_float_money_and_score(field: str, value: float) -> None:
    with pytest.raises(ValidationError):
        _comparison_entry(**{field: value})


def test_quote_comparison_round_trips_ranked_entries() -> None:
    comparison = QuoteComparisonDTO(
        comparison_id="cmp_001",
        procurement_request_id="pr_demo",
        entries=[_comparison_entry()],
        recommended_quote_id="quo_alpha_v1",
        generated_at=FIXED_NOW,
        version=1,
    )

    assert QuoteComparisonDTO.model_validate_json(comparison.model_dump_json()) == comparison


def test_request_approval_binds_exact_quote_version() -> None:
    command = RequestApprovalCommand(
        procurement_request_id="pr_demo",
        comparison_id="cmp_001",
        quote_id="quo_alpha_v1",
        quote_version=4,
        approver_user_id="usr_approver",
        requested_by_actor_type=ActorType.AGENT,
        requested_by_actor_id="agent_buyer",
        idempotency_key="idem-approval-001",
    )

    assert command.quote_id == "quo_alpha_v1"
    assert command.quote_version == 4


def test_approval_status_round_trip_keeps_quote_binding() -> None:
    approval = ApprovalDTO(
        approval_id="apr_001",
        procurement_request_id="pr_demo",
        quote_id="quo_alpha_v1",
        quote_version=4,
        approver_user_id="usr_approver",
        status=ApprovalStatus.APPROVED,
        requested_at=FIXED_NOW,
        decided_at=FIXED_NOW,
        decision_reason="Best eligible option",
        version=2,
    )

    assert ApprovalDTO.model_validate_json(approval.model_dump_json()) == approval


def test_send_award_binds_approval_and_exact_quote_version() -> None:
    command = SendAwardCommand(
        procurement_request_id="pr_demo",
        approval_id="apr_001",
        supplier_id="sup_alpha",
        approved_quote_id="quo_alpha_v1",
        approved_quote_version=4,
        idempotency_key="idem-award-001",
    )

    assert command.approval_id == "apr_001"
    assert command.approved_quote_version == 4


def test_award_round_trip_preserves_approved_terms_snapshot() -> None:
    award = AwardDTO(
        award_id="awd_001",
        procurement_request_id="pr_demo",
        supplier_id="sup_alpha",
        approved_quote_id="quo_alpha_v1",
        approved_quote_version=4,
        approved_total_cents=420_000,
        terms_snapshot={"total_cents": 420_000, "people_count": 80},
        approval_id="apr_001",
        status=AwardStatus.SENT,
        created_at=FIXED_NOW,
        sent_at=FIXED_NOW,
        version=2,
    )

    assert AwardDTO.model_validate_json(award.model_dump_json()) == award
