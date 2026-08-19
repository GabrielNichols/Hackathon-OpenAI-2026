"""Typed contracts for the buyer's procurement request.

The models in this module deliberately distinguish an incomplete draft from a
request which has passed the deterministic readiness checks.  Natural-language
interpreters only produce patches; they never get authority to manufacture a
``READY`` request or change its lifecycle status.
"""

from __future__ import annotations

from datetime import date, datetime, time
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

NonNegativeInt = Annotated[int, Field(strict=True, ge=0)]
PositiveInt = Annotated[int, Field(strict=True, gt=0)]
RequestCategory = Literal["corporate_catering"]
Currency = Literal["BRL"]
ApprovalCheckpoint = Literal["before_award"]
MAX_BUYER_MESSAGE_CHARS = 4_000


class ProcurementRequestStatus(StrEnum):
    """Persisted lifecycle state of a procurement request.

    This enum is intentionally a domain status only.  Reasons why an agent run
    stopped live in ``procurement_agent`` and must not be written into this
    field.
    """

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


class ProcurementModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        validate_assignment=True,
    )


class ProcurementRequestPatch(ProcurementModel):
    """Fields supported by one interpreted message or an explicit edit.

    Every field is optional so ``model_fields_set`` can distinguish an omitted
    value from a deliberate value such as ``False`` or ``0``.
    """

    category: RequestCategory | None = None
    description: Annotated[str, Field(min_length=1, max_length=MAX_BUYER_MESSAGE_CHARS)] | None = (
        None
    )
    event_date: date | None = None
    delivery_time: time | None = None
    location_city: Annotated[str, Field(min_length=1, max_length=120)] | None = None
    location_district: Annotated[str, Field(min_length=1, max_length=120)] | None = None
    full_address: Annotated[str, Field(min_length=1, max_length=500)] | None = None
    people_count: PositiveInt | None = None
    maximum_total_cents: PositiveInt | None = None
    currency: Currency | None = None
    vegetarian_count: NonNegativeInt | None = None
    vegan_count: NonNegativeInt | None = None
    gluten_free_count: NonNegativeInt | None = None
    invoice_required: bool | None = None
    no_single_use_plastic: bool | None = None
    response_deadline: datetime | None = None
    desired_quote_count: Annotated[int, Field(strict=True, ge=1, le=20)] | None = None
    approver_user_id: Annotated[str, Field(min_length=1, max_length=200)] | None = None

    @model_validator(mode="after")
    def validate_aware_deadline(self) -> ProcurementRequestPatch:
        if self.response_deadline is not None and (
            self.response_deadline.tzinfo is None or self.response_deadline.utcoffset() is None
        ):
            raise ValueError("response_deadline must be timezone-aware")
        return self


class ProcurementRequestDraft(ProcurementModel):
    """A versioned request which may still need clarification."""

    request_id: Annotated[str, Field(min_length=1, max_length=100)]
    status: Literal[
        ProcurementRequestStatus.DRAFT,
        ProcurementRequestStatus.NEEDS_CLARIFICATION,
    ] = ProcurementRequestStatus.DRAFT
    version: PositiveInt = 1
    category: RequestCategory | None = None
    description: Annotated[str, Field(min_length=1, max_length=MAX_BUYER_MESSAGE_CHARS)] | None = (
        None
    )
    event_date: date | None = None
    delivery_time: time | None = None
    location_city: Annotated[str, Field(min_length=1, max_length=120)] | None = None
    location_district: Annotated[str, Field(min_length=1, max_length=120)] | None = None
    full_address: Annotated[str, Field(min_length=1, max_length=500)] | None = None
    people_count: PositiveInt | None = None
    maximum_total_cents: PositiveInt | None = None
    currency: Currency = "BRL"
    vegetarian_count: NonNegativeInt = 0
    vegan_count: NonNegativeInt = 0
    gluten_free_count: NonNegativeInt = 0
    invoice_required: bool | None = None
    no_single_use_plastic: bool | None = None
    response_deadline: datetime | None = None
    desired_quote_count: Annotated[int, Field(strict=True, ge=1, le=20)] | None = None
    approver_user_id: Annotated[str, Field(min_length=1, max_length=200)] | None = None

    @model_validator(mode="after")
    def validate_aware_deadline(self) -> ProcurementRequestDraft:
        if self.response_deadline is not None and (
            self.response_deadline.tzinfo is None or self.response_deadline.utcoffset() is None
        ):
            raise ValueError("response_deadline must be timezone-aware")
        return self


class ProcurementRequestReady(ProcurementModel):
    """A request whose blocking facts are present and internally consistent."""

    request_id: Annotated[str, Field(min_length=1, max_length=100)]
    status: Literal[ProcurementRequestStatus.READY] = ProcurementRequestStatus.READY
    version: PositiveInt
    category: RequestCategory
    description: Annotated[str, Field(min_length=1, max_length=MAX_BUYER_MESSAGE_CHARS)]
    event_date: date
    delivery_time: time | None = None
    location_city: Annotated[str, Field(min_length=1, max_length=120)] | None = None
    location_district: Annotated[str, Field(min_length=1, max_length=120)] | None = None
    full_address: Annotated[str, Field(min_length=1, max_length=500)] | None = None
    people_count: PositiveInt
    maximum_total_cents: PositiveInt | None = None
    currency: Currency = "BRL"
    vegetarian_count: NonNegativeInt = 0
    vegan_count: NonNegativeInt = 0
    gluten_free_count: NonNegativeInt = 0
    invoice_required: bool | None = None
    no_single_use_plastic: bool | None = None
    response_deadline: datetime
    desired_quote_count: Annotated[int, Field(strict=True, ge=1, le=20)] | None = None
    approver_user_id: Annotated[str, Field(min_length=1, max_length=200)]

    @model_validator(mode="after")
    def validate_ready_invariants(self) -> ProcurementRequestReady:
        if not self.full_address and not self.location_district:
            raise ValueError("a full address or district is required")
        if self.response_deadline.tzinfo is None or self.response_deadline.utcoffset() is None:
            raise ValueError("response_deadline must be timezone-aware")
        dietary_total = self.vegetarian_count + self.vegan_count + self.gluten_free_count
        if dietary_total > self.people_count:
            raise ValueError("dietary counts cannot exceed people_count")
        return self


class ProcurementPolicySnapshot(ProcurementModel):
    """Immutable policy values used to assess and plan one request."""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        validate_assignment=True,
        frozen=True,
    )

    policy_id: Annotated[str, Field(min_length=1, max_length=100)] = "procurement_default_v1"
    version: PositiveInt = 1
    budget_is_eliminatory: bool = True
    delivery_time_is_required: bool = True
    invoice_answer_is_required: bool = True
    plastic_answer_is_required: bool = True
    default_location_city: Annotated[str, Field(min_length=1, max_length=120)] | None = "São Paulo"
    default_target_supplier_count: Annotated[int, Field(strict=True, ge=1, le=20)] = 3
    maximum_target_supplier_count: Annotated[int, Field(strict=True, ge=1, le=20)] = 5
    negotiation_enabled: bool = False
    target_total_cents: PositiveInt | None = None
    target_budget_percent: Annotated[int, Field(strict=True, ge=1, le=100)] = 90
    maximum_negotiation_rounds: Annotated[int, Field(strict=True, ge=0, le=10)] = 2
    allowed_negotiation_topics: tuple[str, ...] = (
        "total_price",
        "delivery_fee",
        "included_items",
        "payment_term",
    )
    maximum_follow_ups: Annotated[int, Field(strict=True, ge=0, le=10)] = 2
    approval_checkpoint: ApprovalCheckpoint = "before_award"
    ranking_weights: dict[str, NonNegativeInt] = Field(
        default_factory=lambda: {
            "total_price": 50,
            "mandatory_requirements": 35,
            "response_time": 15,
        }
    )

    @model_validator(mode="after")
    def validate_policy(self) -> ProcurementPolicySnapshot:
        if self.default_target_supplier_count > self.maximum_target_supplier_count:
            raise ValueError(
                "default_target_supplier_count cannot exceed maximum_target_supplier_count"
            )
        if sum(self.ranking_weights.values()) != 100:
            raise ValueError("ranking_weights must sum to 100")
        if not self.allowed_negotiation_topics and self.negotiation_enabled:
            raise ValueError(
                "allowed_negotiation_topics cannot be empty when negotiation is enabled"
            )
        return self


class ProcurementPlan(ProcurementModel):
    """Buyer-reviewable plan produced from a ready request and policy snapshot."""

    request_id: Annotated[str, Field(min_length=1, max_length=100)]
    target_supplier_count: Annotated[int, Field(strict=True, ge=1, le=20)]
    eliminatory_criteria: list[Annotated[str, Field(min_length=1)]]
    ranking_weights: dict[str, NonNegativeInt]
    response_deadline: datetime
    negotiation_enabled: bool
    target_total_cents: PositiveInt | None = None
    maximum_negotiation_rounds: Annotated[int, Field(strict=True, ge=0, le=10)]
    allowed_negotiation_topics: list[Annotated[str, Field(min_length=1)]]
    maximum_follow_ups: Annotated[int, Field(strict=True, ge=0, le=10)]
    approval_checkpoint: ApprovalCheckpoint = "before_award"
    policy_snapshot: ProcurementPolicySnapshot
    version: PositiveInt = 1

    @model_validator(mode="after")
    def validate_plan(self) -> ProcurementPlan:
        if self.response_deadline.tzinfo is None or self.response_deadline.utcoffset() is None:
            raise ValueError("response_deadline must be timezone-aware")
        if sum(self.ranking_weights.values()) != 100:
            raise ValueError("ranking_weights must sum to 100")
        if self.target_supplier_count > self.policy_snapshot.maximum_target_supplier_count:
            raise ValueError("target_supplier_count exceeds policy maximum")
        if not self.negotiation_enabled:
            if self.maximum_negotiation_rounds != 0:
                raise ValueError(
                    "maximum_negotiation_rounds must be zero when negotiation is disabled"
                )
            if self.allowed_negotiation_topics:
                raise ValueError(
                    "allowed_negotiation_topics must be empty when negotiation is disabled"
                )
        return self


class ProcurementPlanPatch(ProcurementModel):
    target_supplier_count: Annotated[int, Field(strict=True, ge=1, le=20)] | None = None
    eliminatory_criteria: list[Annotated[str, Field(min_length=1)]] | None = None
    ranking_weights: dict[str, NonNegativeInt] | None = None
    response_deadline: datetime | None = None
    negotiation_enabled: bool | None = None
    target_total_cents: PositiveInt | None = None
    maximum_negotiation_rounds: Annotated[int, Field(strict=True, ge=0, le=10)] | None = None
    allowed_negotiation_topics: list[Annotated[str, Field(min_length=1)]] | None = None
    maximum_follow_ups: Annotated[int, Field(strict=True, ge=0, le=10)] | None = None


class FieldConflict(ProcurementModel):
    """A candidate fact which disagrees with the current request."""

    field: str
    current_value: Any
    candidate_value: Any
    candidate_evidence: str
    reason_code: Literal["CONFLICTING_CONFIRMED_VALUE"] = "CONFLICTING_CONFIRMED_VALUE"


class InterpretationProviderMetadata(ProcurementModel):
    """Safe observability fields; raw prompts, outputs and credentials are excluded."""

    provider: Literal["openai", "local_fallback"]
    model: Annotated[str, Field(min_length=1, max_length=200)] | None = None
    response_id: Annotated[str, Field(min_length=1, max_length=200)] | None = None
    prompt_version: Annotated[str, Field(min_length=1, max_length=100)]
    prompt_sha256: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
    input_sha256: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")] | None = None
    output_sha256: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")] | None = None
    schema_sha256: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")] | None = None
    input_tokens: NonNegativeInt | None = None
    output_tokens: NonNegativeInt | None = None
    fallback_reason_code: Annotated[str, Field(min_length=1, max_length=100)] | None = None


class ProcurementInterpretationResult(ProcurementModel):
    """Pure interpretation output; it carries facts but performs no write."""

    extracted_fields: ProcurementRequestPatch = Field(default_factory=ProcurementRequestPatch)
    evidence: dict[str, str] = Field(default_factory=dict)
    ambiguities: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    conflicts: list[FieldConflict] = Field(default_factory=list)
    missing_required_fields: list[str] = Field(default_factory=list)
    confidence_by_field: dict[str, Annotated[float, Field(ge=0.0, le=1.0)]] = Field(
        default_factory=dict
    )
    provider_metadata: InterpretationProviderMetadata | None = None

    @property
    def has_conflicts(self) -> bool:
        return bool(self.conflicts)


class Clarification(ProcurementModel):
    fields: list[str]
    question: str
    reason_code: str


class RequestAssessment(ProcurementModel):
    """Deterministic readiness decision, independent of an agent stop reason."""

    status: Literal[
        ProcurementRequestStatus.NEEDS_CLARIFICATION,
        ProcurementRequestStatus.READY,
    ]
    missing_required_fields: list[str] = Field(default_factory=list)
    blocking_issues: list[str] = Field(default_factory=list)
    clarifications: list[Clarification] = Field(default_factory=list)
    can_start_sourcing: bool
    ready_request: ProcurementRequestReady | None = None

    @model_validator(mode="after")
    def validate_consistency(self) -> RequestAssessment:
        if self.can_start_sourcing != (self.status is ProcurementRequestStatus.READY):
            raise ValueError("can_start_sourcing must match READY status")
        if self.can_start_sourcing and (
            self.missing_required_fields or self.blocking_issues or self.ready_request is None
        ):
            raise ValueError("a ready assessment cannot contain blockers")
        if not self.can_start_sourcing and self.ready_request is not None:
            raise ValueError("a blocked assessment cannot contain ready_request")
        return self


RequestLike = ProcurementRequestDraft | ProcurementRequestReady


__all__ = [
    "MAX_BUYER_MESSAGE_CHARS",
    "Clarification",
    "FieldConflict",
    "InterpretationProviderMetadata",
    "ProcurementInterpretationResult",
    "ProcurementPlan",
    "ProcurementPlanPatch",
    "ProcurementPolicySnapshot",
    "ProcurementRequestDraft",
    "ProcurementRequestPatch",
    "ProcurementRequestReady",
    "ProcurementRequestStatus",
    "RequestAssessment",
    "RequestLike",
]
