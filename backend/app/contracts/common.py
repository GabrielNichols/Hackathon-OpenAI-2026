"""Stable primitives and envelopes shared by every backend module.

This module deliberately has no dependency on an ORM, web framework, clock, or
external service.  Contract values are validated at the boundary and remain
ordinary Pydantic data transfer objects.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Any

from pydantic import (
    AfterValidator,
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
)

CONTRACT_VERSION = "0.1.0"


def _normalize_utc(value: datetime) -> datetime:
    """Normalize an already-aware timestamp to the internal UTC convention."""

    return value.astimezone(UTC)


type ContractString = Annotated[
    str,
    StringConstraints(min_length=1, max_length=512),
]
type EntityId = Annotated[
    str,
    StringConstraints(
        min_length=3,
        max_length=128,
        pattern=r"^[a-z][a-z0-9]*_[A-Za-z0-9][A-Za-z0-9_-]*$",
    ),
]
type SupplierId = Annotated[
    str,
    StringConstraints(pattern=r"^sup_[A-Za-z0-9][A-Za-z0-9_-]*$"),
]
type ProcurementRequestId = Annotated[
    str,
    StringConstraints(pattern=r"^pr_[A-Za-z0-9][A-Za-z0-9_-]*$"),
]
type RFQId = Annotated[
    str,
    StringConstraints(pattern=r"^rfq_[A-Za-z0-9][A-Za-z0-9_-]*$"),
]
type QuoteId = Annotated[
    str,
    StringConstraints(pattern=r"^quo_[A-Za-z0-9][A-Za-z0-9_-]*$"),
]
type ApprovalId = Annotated[
    str,
    StringConstraints(pattern=r"^apr_[A-Za-z0-9][A-Za-z0-9_-]*$"),
]
type AwardId = Annotated[
    str,
    StringConstraints(pattern=r"^awd_[A-Za-z0-9][A-Za-z0-9_-]*$"),
]
type MoneyCents = Annotated[int, Field(strict=True, ge=0)]
type Version = Annotated[int, Field(strict=True, ge=0)]
type NonNegativeCount = Annotated[int, Field(strict=True, ge=0)]
type PositiveCount = Annotated[int, Field(strict=True, gt=0)]
type ScorePoints = Annotated[int, Field(strict=True, ge=0, le=100)]
type IdempotencyKey = Annotated[
    str,
    StringConstraints(
        min_length=1,
        max_length=200,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$",
    ),
]
type CorrelationId = Annotated[
    str,
    StringConstraints(pattern=r"^cor_[A-Za-z0-9][A-Za-z0-9_-]*$"),
]
type EventType = Annotated[
    str,
    StringConstraints(pattern=r"^[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)*$"),
]
type StableCode = EventType
type ActionName = Annotated[
    str,
    StringConstraints(pattern=r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$"),
]
type AggregateType = ActionName
type UtcDateTime = Annotated[AwareDatetime, AfterValidator(_normalize_utc)]


class ContractModel(BaseModel):
    """Strict base configuration for the v0 wire contract."""

    model_config = ConfigDict(extra="forbid", validate_default=True)


class ActorType(StrEnum):
    HUMAN = "human"
    SUPPLIER = "supplier"
    AGENT = "agent"
    SYSTEM = "system"
    EXTERNAL_SERVICE = "external_service"


class ErrorCode(StrEnum):
    VALIDATION_ERROR = "VALIDATION_ERROR"
    NOT_FOUND = "NOT_FOUND"
    CONFLICT = "CONFLICT"
    INVALID_STATE_TRANSITION = "INVALID_STATE_TRANSITION"
    POLICY_DENIED = "POLICY_DENIED"
    LINK_EXPIRED = "LINK_EXPIRED"
    LINK_INVALID = "LINK_INVALID"
    IDEMPOTENCY_CONFLICT = "IDEMPOTENCY_CONFLICT"
    OPTIMISTIC_LOCK_CONFLICT = "OPTIMISTIC_LOCK_CONFLICT"
    EXTERNAL_DELIVERY_NOT_CONFIRMED = "EXTERNAL_DELIVERY_NOT_CONFIRMED"


class SupplierState(StrEnum):
    DRAFT = "DRAFT"
    MATERIALS_UPLOADED = "MATERIALS_UPLOADED"
    EXTRACTED = "EXTRACTED"
    AWAITING_SUPPLIER_REVIEW = "AWAITING_SUPPLIER_REVIEW"
    CONFIRMED = "CONFIRMED"
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"
    EXPIRED = "EXPIRED"


class ProcurementRequestState(StrEnum):
    DRAFT = "DRAFT"
    NEEDS_CLARIFICATION = "NEEDS_CLARIFICATION"
    READY = "READY"
    SOURCING = "SOURCING"
    RFQ_ACTIVE = "RFQ_ACTIVE"
    QUOTES_UNDER_REVIEW = "QUOTES_UNDER_REVIEW"
    NEGOTIATING = "NEGOTIATING"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    APPROVED = "APPROVED"
    AWARD_SENT = "AWARD_SENT"
    SUPPLIER_ACCEPTED = "SUPPLIER_ACCEPTED"
    READY_FOR_CONTRACTING = "READY_FOR_CONTRACTING"
    CLOSED = "CLOSED"
    CANCELLED = "CANCELLED"
    NO_ELIGIBLE_SUPPLIERS = "NO_ELIGIBLE_SUPPLIERS"
    NO_VALID_QUOTES = "NO_VALID_QUOTES"
    APPROVAL_REJECTED = "APPROVAL_REJECTED"
    SUPPLIER_DECLINED_AWARD = "SUPPLIER_DECLINED_AWARD"
    EXPIRED = "EXPIRED"


class QuoteState(StrEnum):
    REQUESTED = "REQUESTED"
    OPENED = "OPENED"
    DRAFT_RESPONSE = "DRAFT_RESPONSE"
    SUBMITTED = "SUBMITTED"
    VALIDATING = "VALIDATING"
    NEEDS_CLARIFICATION = "NEEDS_CLARIFICATION"
    VALID = "VALID"
    NEGOTIATING = "NEGOTIATING"
    FINAL = "FINAL"
    SELECTED = "SELECTED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"


class ApprovalStatus(StrEnum):
    REQUESTED = "REQUESTED"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    CHANGES_REQUESTED = "CHANGES_REQUESTED"


class AwardStatus(StrEnum):
    CREATED = "CREATED"
    SENT = "SENT"
    ACCEPTED = "ACCEPTED"
    DECLINED = "DECLINED"


class RFQDeliveryStatus(StrEnum):
    PENDING = "PENDING"
    QUEUED = "QUEUED"
    SENT_TO_GATEWAY = "SENT_TO_GATEWAY"
    DELIVERED = "DELIVERED"
    FAILED = "FAILED"


class OutboxStatus(StrEnum):
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    DELIVERED = "DELIVERED"
    FAILED = "FAILED"


class DeliveryChannel(StrEnum):
    EMAIL = "email"
    MANUAL_LINK = "manual_link"


class ErrorDetailDTO(ContractModel):
    code: ErrorCode
    message: ContractString
    details: dict[str, Any] = Field(default_factory=dict)
    correlation_id: CorrelationId


class ErrorEnvelopeDTO(ContractModel):
    error: ErrorDetailDTO


class AuditEventDTO(ContractModel):
    event_id: EntityId
    event_type: EventType
    aggregate_type: AggregateType
    aggregate_id: EntityId
    actor_type: ActorType
    actor_id: EntityId | None
    occurred_at: UtcDateTime
    previous_state: str | None
    new_state: str | None
    correlation_id: CorrelationId
    causation_id: EntityId | None
    agent_run_id: EntityId | None
    idempotency_key: IdempotencyKey | None
    payload: dict[str, Any] = Field(default_factory=dict)
