"""Versioned DTOs at the Dev 3 -> Dev 4 execution boundary.

The models deliberately contain no persistence or workflow behavior.  They can
be consumed by in-memory adapters today and by Dev 1 repositories/events later.
"""

from __future__ import annotations

from datetime import date, datetime, time
from enum import StrEnum
from typing import Annotated, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from backend.app.shared.runtime import ensure_utc

MoneyCents = Annotated[int, Field(strict=True, ge=0)]
NonNegativeInt = Annotated[int, Field(strict=True, ge=0)]
PositiveInt = Annotated[int, Field(strict=True, ge=1)]
ScoreBasisPoints = Annotated[int, Field(strict=True, ge=0, le=10_000)]
PercentageWeight = Annotated[int, Field(strict=True, ge=0, le=100)]
NonEmptyString = Annotated[str, Field(min_length=1)]


class ContractDTO(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        validate_assignment=True,
        validate_default=True,
        use_enum_values=True,
    )

    @field_validator("*", mode="after")
    @classmethod
    def _timestamps_are_utc(cls, value: object) -> object:
        if isinstance(value, datetime):
            return ensure_utc(value)
        return value


class ActorType(StrEnum):
    AGENT = "agent"
    HUMAN = "human"
    SYSTEM = "system"


class DeliveryChannel(StrEnum):
    EMAIL = "email"
    MANUAL_LINK = "manual_link"


class RFQRoundStatus(StrEnum):
    DRAFT = "DRAFT"
    DISPATCHING = "DISPATCHING"
    ACTIVE = "ACTIVE"
    CLOSED = "CLOSED"
    FAILED = "FAILED"


class DeliveryStatus(StrEnum):
    PENDING = "PENDING"
    QUEUED = "QUEUED"
    SENT_TO_GATEWAY = "SENT_TO_GATEWAY"
    DELIVERED = "DELIVERED"
    FAILED = "FAILED"


class DietaryStatus(StrEnum):
    CONFIRMED = "confirmed"
    PARTIAL = "partial"
    UNKNOWN = "unknown"
    NOT_AVAILABLE = "not_available"


class QuoteStatus(StrEnum):
    DRAFT = "DRAFT"
    NEEDS_CLARIFICATION = "NEEDS_CLARIFICATION"
    FINAL = "FINAL"
    INVALID = "INVALID"
    EXPIRED = "EXPIRED"
    DECLINED = "DECLINED"


class ComparisonStatus(StrEnum):
    READY = "READY"
    NO_ELIGIBLE_QUOTES = "NO_ELIGIBLE_QUOTES"
    NEEDS_MORE_QUOTES = "NEEDS_MORE_QUOTES"


class ApprovalStatus(StrEnum):
    REQUESTED = "REQUESTED"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    CHANGES_REQUESTED = "CHANGES_REQUESTED"
    INVALIDATED = "INVALIDATED"


class AwardStatus(StrEnum):
    QUEUED = "QUEUED"
    SENT_TO_GATEWAY = "SENT_TO_GATEWAY"
    DELIVERED = "DELIVERED"
    ACCEPTED = "ACCEPTED"
    DECLINED = "DECLINED"
    FAILED = "FAILED"


class ReservationStatus(StrEnum):
    NOT_CREATED = "NOT_CREATED"
    PENDING = "PENDING"
    CONFIRMED = "CONFIRMED"
    FAILED = "FAILED"


class CommandContextDTO(ContractDTO):
    contract_version: Literal["dev3-dev4.v0"] = "dev3-dev4.v0"
    idempotency_key: Annotated[str, Field(min_length=1, max_length=200)]
    correlation_id: Annotated[str, Field(min_length=1, max_length=200)]
    causation_id: Annotated[str, Field(min_length=1, max_length=200)] | None = None
    actor_type: ActorType
    actor_id: Annotated[str, Field(min_length=1, max_length=200)]
    agent_run_id: Annotated[str, Field(min_length=1, max_length=200)] | None = None


class RFQRequirementsSnapshotDTO(ContractDTO):
    description: NonEmptyString
    category: NonEmptyString
    event_date: date
    delivery_time: time
    timezone: NonEmptyString
    location_city: NonEmptyString
    location_district: str | None = None
    full_address: str | None = None
    people_count: PositiveInt
    maximum_total_cents: MoneyCents | None = None
    currency: Literal["BRL"] = "BRL"
    vegetarian_count: NonNegativeInt = 0
    vegan_count: NonNegativeInt = 0
    gluten_free_count: NonNegativeInt = 0
    invoice_required: bool | None = None
    no_single_use_plastic: bool | None = None
    mandatory_requirements: list[NonEmptyString] = Field(default_factory=list)

    @field_validator("mandatory_requirements")
    @classmethod
    def _requirements_are_unique(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("mandatory_requirements must be unique")
        return value

    @model_validator(mode="after")
    def _dietary_counts_fit_people_count(self) -> RFQRequirementsSnapshotDTO:
        if any(
            count > self.people_count
            for count in (
                self.vegetarian_count,
                self.vegan_count,
                self.gluten_free_count,
            )
        ):
            raise ValueError("a dietary count cannot exceed people_count")
        return self


class ExecutionPolicySnapshotDTO(ContractDTO):
    source_policy_version: PositiveInt
    minimum_confirmed_deliveries: PositiveInt = 1
    maximum_follow_ups: NonNegativeInt = 0
    maximum_total_cents: MoneyCents | None = None
    target_total_cents: MoneyCents | None = None
    ranking_weights: dict[NonEmptyString, PercentageWeight]
    negotiation_enabled: bool = False
    maximum_negotiation_rounds: NonNegativeInt = 0
    allowed_negotiation_topics: list[NonEmptyString] = Field(default_factory=list)
    approver_user_id: NonEmptyString

    @field_validator("ranking_weights")
    @classmethod
    def _weights_sum_to_one_hundred(cls, value: dict[str, int]) -> dict[str, int]:
        if not value:
            raise ValueError("ranking_weights must not be empty")
        if sum(value.values()) != 100:
            raise ValueError("ranking_weights must sum to 100")
        return value

    @model_validator(mode="after")
    def _target_does_not_exceed_maximum(self) -> ExecutionPolicySnapshotDTO:
        if (
            self.target_total_cents is not None
            and self.maximum_total_cents is not None
            and self.target_total_cents > self.maximum_total_cents
        ):
            raise ValueError("target_total_cents cannot exceed maximum_total_cents")
        if not self.negotiation_enabled and self.maximum_negotiation_rounds != 0:
            raise ValueError("maximum_negotiation_rounds must be zero when negotiation is disabled")
        return self


class CreateRFQRoundCommand(ContractDTO):
    context: CommandContextDTO
    procurement_request_id: NonEmptyString
    request_version: PositiveInt
    plan_version: PositiveInt
    recipient_supplier_ids: Annotated[list[NonEmptyString], Field(min_length=1)]
    response_deadline: datetime
    requirements: RFQRequirementsSnapshotDTO
    execution_policy: ExecutionPolicySnapshotDTO

    @field_validator("recipient_supplier_ids")
    @classmethod
    def _recipients_are_unique(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("recipient_supplier_ids must be unique")
        return value

    @model_validator(mode="after")
    def _activation_threshold_fits_recipient_count(self) -> CreateRFQRoundCommand:
        if self.execution_policy.minimum_confirmed_deliveries > len(self.recipient_supplier_ids):
            raise ValueError("minimum_confirmed_deliveries cannot exceed recipient count")
        return self


class RFQRoundDTO(ContractDTO):
    rfq_round_id: NonEmptyString
    procurement_request_id: NonEmptyString
    request_version: PositiveInt
    round_version: PositiveInt
    status: RFQRoundStatus = RFQRoundStatus.DRAFT
    recipient_count: NonNegativeInt
    response_deadline: datetime
    requirements_snapshot_hash: NonEmptyString
    policy_snapshot_hash: NonEmptyString
    created_at: datetime
    idempotent_replay: bool = False


class SendRFQRoundCommand(ContractDTO):
    context: CommandContextDTO
    rfq_round_id: NonEmptyString
    expected_round_version: PositiveInt
    channel: DeliveryChannel


class DeliveryDTO(ContractDTO):
    recipient_id: NonEmptyString
    rfq_round_id: NonEmptyString
    supplier_id: NonEmptyString
    channel: DeliveryChannel
    status: DeliveryStatus = DeliveryStatus.PENDING
    external_id: str | None = None
    delivered_at: datetime | None = None
    failure_code: str | None = None


class DeliveryBatchDTO(ContractDTO):
    rfq_round_id: NonEmptyString
    round_version: PositiveInt
    deliveries: list[DeliveryDTO]
    confirmed_count: NonNegativeInt
    all_confirmed: bool
    activation_criteria_met: bool
    updated_at: datetime
    idempotent_replay: bool = False


class RFQResponseContextDTO(ContractDTO):
    rfq_round_id: NonEmptyString
    recipient_id: NonEmptyString
    supplier_id: NonEmptyString
    requirements: RFQRequirementsSnapshotDTO
    response_deadline: datetime


class QuoteSubmissionDTO(ContractDTO):
    availability_confirmed: bool
    subtotal_cents: MoneyCents
    delivery_fee_cents: MoneyCents = 0
    other_fee_cents: MoneyCents = 0
    total_cents: MoneyCents
    included_items: list[NonEmptyString]
    substitutions: list[NonEmptyString] = Field(default_factory=list)
    invoice_available: bool | None
    vegetarian_status: DietaryStatus
    vegan_status: DietaryStatus
    gluten_free_status: DietaryStatus
    cross_contamination_warning: str | None = None
    valid_until: datetime
    cancellation_terms: NonEmptyString
    respondent_name: NonEmptyString
    respondent_contact: NonEmptyString
    supplier_confirmation: bool
    sustainability_score: Annotated[int, Field(strict=True, ge=0, le=5)] = 0
    history_score: Annotated[int, Field(strict=True, ge=0, le=5)] = 0
    response_time_minutes: NonNegativeInt = 0


class QuoteDTO(QuoteSubmissionDTO):
    quote_id: NonEmptyString
    quote_version: PositiveInt
    rfq_round_id: NonEmptyString
    recipient_id: NonEmptyString
    supplier_id: NonEmptyString
    status: QuoteStatus
    price_per_person_cents: MoneyCents
    eligible: bool
    validation_errors: list[str] = Field(default_factory=list)
    validation_warnings: list[str] = Field(default_factory=list)
    submitted_at: datetime


class QuoteCollectionStatusDTO(ContractDTO):
    rfq_round_id: NonEmptyString
    collection_version: NonNegativeInt
    response_deadline: datetime
    expected_count: NonNegativeInt
    submitted_count: NonNegativeInt
    valid_count: NonNegativeInt
    needs_clarification_count: NonNegativeInt
    declined_count: NonNegativeInt
    pending_count: NonNegativeInt
    ready_for_comparison: bool
    updated_at: datetime


class QuoteRefDTO(ContractDTO):
    quote_id: NonEmptyString
    quote_version: PositiveInt


class CompareQuotesCommand(ContractDTO):
    context: CommandContextDTO
    procurement_request_id: NonEmptyString
    rfq_round_id: NonEmptyString
    expected_quote_collection_version: NonNegativeInt


class ScoreComponentDTO(ContractDTO):
    criterion: NonEmptyString
    weight: PercentageWeight
    normalized_score_basis_points: ScoreBasisPoints
    points_basis_points: ScoreBasisPoints
    reason: str | None = None
    evidence_refs: list[str] = Field(default_factory=list)


class QuoteCandidateDTO(ContractDTO):
    quote_id: NonEmptyString
    quote_version: PositiveInt
    supplier_id: NonEmptyString
    eligible: bool
    total_cents: MoneyCents
    currency: Literal["BRL"] = "BRL"
    price_per_person_cents: MoneyCents
    score_basis_points: ScoreBasisPoints
    score_components: list[ScoreComponentDTO]
    disqualification_reasons: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)


class QuoteComparisonDTO(ContractDTO):
    comparison_id: NonEmptyString
    comparison_version: PositiveInt
    procurement_request_id: NonEmptyString
    rfq_round_id: NonEmptyString
    quote_collection_version: NonNegativeInt
    status: ComparisonStatus
    candidates: list[QuoteCandidateDTO]
    recommended_quote: QuoteRefDTO | None = None
    input_hash: NonEmptyString
    created_at: datetime
    idempotent_replay: bool = False


class RequestApprovalCommand(ContractDTO):
    context: CommandContextDTO
    procurement_request_id: NonEmptyString
    comparison_id: NonEmptyString
    comparison_version: PositiveInt
    selected_quote: QuoteRefDTO
    approver_user_id: NonEmptyString


class ApprovalDTO(ContractDTO):
    approval_id: NonEmptyString
    approval_version: PositiveInt
    status: ApprovalStatus
    procurement_request_id: NonEmptyString
    comparison_id: NonEmptyString
    comparison_version: PositiveInt
    selected_quote: QuoteRefDTO
    approver_user_id: NonEmptyString
    decided_by_user_id: str | None = None
    reason: str | None = None
    requested_at: datetime
    decided_at: datetime | None = None
    idempotent_replay: bool = False


class SendAwardCommand(ContractDTO):
    context: CommandContextDTO
    procurement_request_id: NonEmptyString
    approval_id: NonEmptyString
    expected_approval_version: PositiveInt


class AwardDTO(ContractDTO):
    award_id: NonEmptyString
    award_version: PositiveInt
    procurement_request_id: NonEmptyString
    supplier_id: NonEmptyString
    approved_quote: QuoteRefDTO
    approval_id: NonEmptyString
    approved_total_cents: MoneyCents
    currency: Literal["BRL"] = "BRL"
    status: AwardStatus
    reservation_status: ReservationStatus = ReservationStatus.NOT_CREATED
    ready_for_contracting: bool = False
    delivered_at: datetime | None = None
    responded_at: datetime | None = None
    updated_at: datetime
    idempotent_replay: bool = False


class ReservationDTO(ContractDTO):
    reservation_id: NonEmptyString
    award_id: NonEmptyString
    supplier_id: NonEmptyString
    procurement_request_id: NonEmptyString
    event_date: date
    delivery_window: NonEmptyString
    people_count: PositiveInt
    status: ReservationStatus
    confirmed_by: NonEmptyString
    confirmed_at: datetime


class AuditEventDTO(ContractDTO):
    event_id: NonEmptyString
    event_type: NonEmptyString
    aggregate_type: NonEmptyString
    aggregate_id: NonEmptyString
    occurred_at: datetime
    correlation_id: NonEmptyString
    causation_id: str | None = None
    actor_type: ActorType
    actor_id: NonEmptyString
    payload: dict[str, object] = Field(default_factory=dict)


@runtime_checkable
class RFQExecutionPort(Protocol):
    async def create_round(self, command: CreateRFQRoundCommand) -> RFQRoundDTO: ...

    async def send_round(self, command: SendRFQRoundCommand) -> DeliveryBatchDTO: ...

    async def get_delivery_status(self, rfq_round_id: str) -> DeliveryBatchDTO: ...

    async def get_quote_status(self, rfq_round_id: str) -> QuoteCollectionStatusDTO: ...


@runtime_checkable
class QuoteDecisionPort(Protocol):
    async def compare(self, command: CompareQuotesCommand) -> QuoteComparisonDTO: ...

    async def request_approval(self, command: RequestApprovalCommand) -> ApprovalDTO: ...

    async def get_approval_status(self, approval_id: str) -> ApprovalDTO: ...

    async def send_award(self, command: SendAwardCommand) -> AwardDTO: ...

    async def get_award_status(self, award_id: str) -> AwardDTO: ...


__all__ = [
    "ActorType",
    "ApprovalDTO",
    "ApprovalStatus",
    "AuditEventDTO",
    "AwardDTO",
    "AwardStatus",
    "CommandContextDTO",
    "CompareQuotesCommand",
    "ComparisonStatus",
    "ContractDTO",
    "CreateRFQRoundCommand",
    "DeliveryBatchDTO",
    "DeliveryChannel",
    "DeliveryDTO",
    "DeliveryStatus",
    "DietaryStatus",
    "ExecutionPolicySnapshotDTO",
    "QuoteCandidateDTO",
    "QuoteCollectionStatusDTO",
    "QuoteComparisonDTO",
    "QuoteDTO",
    "QuoteDecisionPort",
    "QuoteRefDTO",
    "QuoteStatus",
    "QuoteSubmissionDTO",
    "RFQExecutionPort",
    "RFQRequirementsSnapshotDTO",
    "RFQResponseContextDTO",
    "RFQRoundDTO",
    "RFQRoundStatus",
    "RequestApprovalCommand",
    "ReservationDTO",
    "ReservationStatus",
    "ScoreComponentDTO",
    "SendAwardCommand",
    "SendRFQRoundCommand",
]
