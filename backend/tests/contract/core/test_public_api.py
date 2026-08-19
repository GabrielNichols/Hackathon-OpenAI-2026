from __future__ import annotations

from app.contracts import (
    CONTRACT_VERSION,
    ActorType,
    ApprovalStatus,
    AwardStatus,
    ErrorCode,
    ProcurementRequestState,
    QuoteDecisionPort,
    QuoteState,
    RFQExecutionPort,
    SupplierDirectoryPort,
    SupplierState,
)


def test_contract_version_is_0_1_0() -> None:
    assert CONTRACT_VERSION == "0.1.0"


def test_state_enums_are_frozen_strings() -> None:
    assert [state.value for state in SupplierState] == [
        "DRAFT",
        "MATERIALS_UPLOADED",
        "EXTRACTED",
        "AWAITING_SUPPLIER_REVIEW",
        "CONFIRMED",
        "ACTIVE",
        "SUSPENDED",
        "EXPIRED",
    ]
    assert [state.value for state in ProcurementRequestState] == [
        "DRAFT",
        "NEEDS_CLARIFICATION",
        "READY",
        "SOURCING",
        "RFQ_ACTIVE",
        "QUOTES_UNDER_REVIEW",
        "NEGOTIATING",
        "AWAITING_APPROVAL",
        "APPROVED",
        "AWARD_SENT",
        "SUPPLIER_ACCEPTED",
        "READY_FOR_CONTRACTING",
        "CLOSED",
        "CANCELLED",
        "NO_ELIGIBLE_SUPPLIERS",
        "NO_VALID_QUOTES",
        "APPROVAL_REJECTED",
        "SUPPLIER_DECLINED_AWARD",
        "EXPIRED",
    ]
    assert [state.value for state in QuoteState] == [
        "REQUESTED",
        "OPENED",
        "DRAFT_RESPONSE",
        "SUBMITTED",
        "VALIDATING",
        "NEEDS_CLARIFICATION",
        "VALID",
        "NEGOTIATING",
        "FINAL",
        "SELECTED",
        "REJECTED",
        "EXPIRED",
    ]
    assert [state.value for state in ApprovalStatus] == [
        "REQUESTED",
        "APPROVED",
        "REJECTED",
        "CHANGES_REQUESTED",
    ]
    assert [state.value for state in AwardStatus] == [
        "CREATED",
        "SENT",
        "ACCEPTED",
        "DECLINED",
    ]


def test_actor_and_error_codes_are_frozen_strings() -> None:
    assert [actor.value for actor in ActorType] == [
        "human",
        "supplier",
        "agent",
        "system",
        "external_service",
    ]
    assert {code.value for code in ErrorCode} == {
        "VALIDATION_ERROR",
        "NOT_FOUND",
        "CONFLICT",
        "INVALID_STATE_TRANSITION",
        "POLICY_DENIED",
        "LINK_EXPIRED",
        "LINK_INVALID",
        "IDEMPOTENCY_CONFLICT",
        "OPTIMISTIC_LOCK_CONFLICT",
        "EXTERNAL_DELIVERY_NOT_CONFIRMED",
    }


def test_shared_ports_are_available_from_the_public_surface() -> None:
    assert SupplierDirectoryPort.__name__ == "SupplierDirectoryPort"
    assert RFQExecutionPort.__name__ == "RFQExecutionPort"
    assert QuoteDecisionPort.__name__ == "QuoteDecisionPort"
