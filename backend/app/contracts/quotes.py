"""Shared quote comparison, negotiation, approval, and award contracts."""

from __future__ import annotations

from typing import Any, Literal, Self

from pydantic import Field, model_validator

from .common import (
    ActorType,
    ApprovalId,
    ApprovalStatus,
    AwardId,
    AwardStatus,
    ContractModel,
    ContractString,
    EntityId,
    IdempotencyKey,
    MoneyCents,
    PositiveCount,
    ProcurementRequestId,
    QuoteId,
    ScorePoints,
    StableCode,
    SupplierId,
    UtcDateTime,
    Version,
)


class QuoteComparisonEntryDTO(ContractModel):
    quote_id: QuoteId
    quote_version: Version
    supplier_id: SupplierId
    eligible: bool
    total_cents: MoneyCents
    currency: Literal["BRL"] = "BRL"
    score: ScorePoints
    rank: PositiveCount
    reason_codes: list[StableCode] = Field(default_factory=list)
    evidence_refs: list[EntityId] = Field(default_factory=list)


class QuoteComparisonDTO(ContractModel):
    comparison_id: EntityId
    procurement_request_id: ProcurementRequestId
    entries: list[QuoteComparisonEntryDTO] = Field(min_length=1)
    recommended_quote_id: QuoteId | None
    generated_at: UtcDateTime
    version: Version

    @model_validator(mode="after")
    def validate_recommendation(self) -> Self:
        if self.recommended_quote_id is None:
            return self
        recommendations = [
            entry
            for entry in self.entries
            if entry.quote_id == self.recommended_quote_id and entry.eligible
        ]
        if not recommendations:
            raise ValueError("recommended_quote_id must reference an eligible entry")
        return self


class NegotiationCommand(ContractModel):
    procurement_request_id: ProcurementRequestId
    quote_id: QuoteId
    quote_version: Version
    topic: ContractString
    requested_change: dict[str, Any]
    idempotency_key: IdempotencyKey


class NegotiationResultDTO(ContractModel):
    negotiation_round_id: EntityId
    quote_id: QuoteId
    quote_version_before: Version
    quote_version_after: Version | None
    status: StableCode
    created_at: UtcDateTime


class RequestApprovalCommand(ContractModel):
    procurement_request_id: ProcurementRequestId
    comparison_id: EntityId
    quote_id: QuoteId
    quote_version: Version
    approver_user_id: EntityId
    requested_by_actor_type: ActorType
    requested_by_actor_id: EntityId | None
    idempotency_key: IdempotencyKey


class ApprovalDTO(ContractModel):
    approval_id: ApprovalId
    procurement_request_id: ProcurementRequestId
    quote_id: QuoteId
    quote_version: Version
    approver_user_id: EntityId
    status: ApprovalStatus
    requested_at: UtcDateTime
    decided_at: UtcDateTime | None = None
    decision_reason: ContractString | None = None
    version: Version

    @model_validator(mode="after")
    def validate_decision_metadata(self) -> Self:
        if self.status is ApprovalStatus.REQUESTED and self.decided_at is not None:
            raise ValueError("REQUESTED approval cannot have decided_at")
        if self.status is not ApprovalStatus.REQUESTED and self.decided_at is None:
            raise ValueError("decided approval status requires decided_at")
        if self.status in {ApprovalStatus.REJECTED, ApprovalStatus.CHANGES_REQUESTED} and not (
            self.decision_reason
        ):
            raise ValueError("rejection or requested changes require a decision_reason")
        return self


class SendAwardCommand(ContractModel):
    procurement_request_id: ProcurementRequestId
    approval_id: ApprovalId
    supplier_id: SupplierId
    approved_quote_id: QuoteId
    approved_quote_version: Version
    idempotency_key: IdempotencyKey


class AwardDTO(ContractModel):
    award_id: AwardId
    procurement_request_id: ProcurementRequestId
    supplier_id: SupplierId
    approved_quote_id: QuoteId
    approved_quote_version: Version
    approved_total_cents: MoneyCents
    terms_snapshot: dict[str, Any]
    approval_id: ApprovalId
    status: AwardStatus
    created_at: UtcDateTime
    sent_at: UtcDateTime | None = None
    version: Version

    @model_validator(mode="after")
    def validate_delivery_timestamp(self) -> Self:
        if self.status is AwardStatus.CREATED and self.sent_at is not None:
            raise ValueError("CREATED award cannot have sent_at")
        if self.status is not AwardStatus.CREATED and self.sent_at is None:
            raise ValueError("sent or answered award requires sent_at")
        return self
