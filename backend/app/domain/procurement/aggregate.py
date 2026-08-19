"""Procurement request lifecycle guarded by material external facts."""

from __future__ import annotations

import hmac
from collections.abc import Iterable
from datetime import datetime
from typing import ClassVar

from app.contracts import ActorType, ErrorCode, ProcurementRequestState
from app.domain.common import (
    AggregateRoot,
    DomainError,
    require_non_empty,
    require_positive_int,
    require_prefixed_id,
    require_utc,
)

REQUIRED_PROCUREMENT_FIELDS = frozenset(
    {
        "category",
        "quantity",
        "event_date",
        "delivery_time",
        "location",
        "maximum_total_cents",
        "mandatory_requirements",
        "response_deadline",
        "approver_id",
    },
)

_PRE_COMMIT_STATES = frozenset(
    {
        ProcurementRequestState.DRAFT,
        ProcurementRequestState.NEEDS_CLARIFICATION,
        ProcurementRequestState.READY,
        ProcurementRequestState.SOURCING,
        ProcurementRequestState.RFQ_ACTIVE,
        ProcurementRequestState.QUOTES_UNDER_REVIEW,
        ProcurementRequestState.NEGOTIATING,
        ProcurementRequestState.AWAITING_APPROVAL,
        ProcurementRequestState.APPROVED,
    },
)


class ProcurementRequestAggregate(AggregateRoot[ProcurementRequestState]):
    aggregate_type: ClassVar[str] = "procurement_request"

    def __init__(self, *, request_id: str, tenant_id: str) -> None:
        super().__init__(
            aggregate_id=require_prefixed_id(
                request_id,
                field="request_id",
                prefix="pr_",
            ),
            tenant_id=tenant_id,
            state=ProcurementRequestState.DRAFT,
            created_event_type="PROCUREMENT_REQUEST_CREATED",
        )
        self.provided_fields: frozenset[str] = frozenset()
        self.selected_quote_id: str | None = None
        self.selected_quote_version: int | None = None
        self.approval_id: str | None = None
        self.award_id: str | None = None
        self.award_terms_hash: str | None = None
        self.acceptance_submission_id: str | None = None
        self.reservation_id: str | None = None

    @classmethod
    def create(cls, *, request_id: str, tenant_id: str) -> ProcurementRequestAggregate:
        return cls(request_id=request_id, tenant_id=tenant_id)

    def request_clarification(self, *, missing_fields: Iterable[str]) -> None:
        self._require_state(
            {ProcurementRequestState.DRAFT}, ProcurementRequestState.NEEDS_CLARIFICATION
        )
        normalized = sorted(
            {require_non_empty(field, field="missing_fields") for field in missing_fields},
        )
        if not normalized:
            raise DomainError(
                ErrorCode.VALIDATION_ERROR,
                "Clarification requires at least one missing field",
                details={"field": "missing_fields"},
            )
        self._transition(
            allowed_from={ProcurementRequestState.DRAFT},
            new_state=ProcurementRequestState.NEEDS_CLARIFICATION,
            event_type="PROCUREMENT_CLARIFICATION_REQUESTED",
            payload={"missing_fields": normalized},
        )

    def mark_ready(self, *, provided_fields: frozenset[str]) -> None:
        self._require_state(
            {ProcurementRequestState.DRAFT, ProcurementRequestState.NEEDS_CLARIFICATION},
            ProcurementRequestState.READY,
        )
        normalized = frozenset(provided_fields)
        missing_fields = sorted(REQUIRED_PROCUREMENT_FIELDS - normalized)
        if missing_fields:
            raise DomainError(
                ErrorCode.VALIDATION_ERROR,
                "Procurement request has missing required fields",
                details={"missing_fields": missing_fields},
            )
        self.provided_fields = normalized
        self._transition(
            allowed_from={
                ProcurementRequestState.DRAFT,
                ProcurementRequestState.NEEDS_CLARIFICATION,
            },
            new_state=ProcurementRequestState.READY,
            event_type="PROCUREMENT_READY",
            payload={"provided_fields": sorted(normalized)},
        )

    def start_sourcing(self) -> None:
        self._transition(
            allowed_from={ProcurementRequestState.READY},
            new_state=ProcurementRequestState.SOURCING,
            event_type="SOURCING_STARTED",
        )

    def mark_no_eligible_suppliers(self) -> None:
        self._transition(
            allowed_from={ProcurementRequestState.SOURCING},
            new_state=ProcurementRequestState.NO_ELIGIBLE_SUPPLIERS,
            event_type="NO_ELIGIBLE_SUPPLIERS",
        )

    def mark_rfq_active(self, *, delivery_ack_id: str) -> None:
        self._require_state({ProcurementRequestState.SOURCING}, ProcurementRequestState.RFQ_ACTIVE)
        delivery_ack_id = self._require_delivery_ack(delivery_ack_id)
        self._transition(
            allowed_from={ProcurementRequestState.SOURCING},
            new_state=ProcurementRequestState.RFQ_ACTIVE,
            event_type="RFQ_DELIVERY_CONFIRMED",
            payload={"delivery_ack_id": delivery_ack_id},
        )

    def begin_quote_review(self, *, submission_id: str) -> None:
        self._require_state(
            {ProcurementRequestState.RFQ_ACTIVE},
            ProcurementRequestState.QUOTES_UNDER_REVIEW,
        )
        submission_id = require_non_empty(submission_id, field="submission_id")
        self._transition(
            allowed_from={ProcurementRequestState.RFQ_ACTIVE},
            new_state=ProcurementRequestState.QUOTES_UNDER_REVIEW,
            event_type="QUOTE_SUBMITTED",
            payload={"submission_id": submission_id},
        )

    def mark_no_valid_quotes(self) -> None:
        self._transition(
            allowed_from={
                ProcurementRequestState.RFQ_ACTIVE,
                ProcurementRequestState.QUOTES_UNDER_REVIEW,
                ProcurementRequestState.NEGOTIATING,
            },
            new_state=ProcurementRequestState.NO_VALID_QUOTES,
            event_type="NO_VALID_QUOTES",
        )

    def start_negotiation(self) -> None:
        self._transition(
            allowed_from={ProcurementRequestState.QUOTES_UNDER_REVIEW},
            new_state=ProcurementRequestState.NEGOTIATING,
            event_type="NEGOTIATION_ROUND_CREATED",
        )

    def request_approval(self, *, quote_id: str, quote_version: int) -> None:
        self._require_state(
            {
                ProcurementRequestState.QUOTES_UNDER_REVIEW,
                ProcurementRequestState.NEGOTIATING,
            },
            ProcurementRequestState.AWAITING_APPROVAL,
        )
        quote_id = require_prefixed_id(quote_id, field="quote_id", prefix="quo_")
        quote_version = require_positive_int(quote_version, field="quote_version")
        self.selected_quote_id = quote_id
        self.selected_quote_version = quote_version
        self._transition(
            allowed_from={
                ProcurementRequestState.QUOTES_UNDER_REVIEW,
                ProcurementRequestState.NEGOTIATING,
            },
            new_state=ProcurementRequestState.AWAITING_APPROVAL,
            event_type="APPROVAL_REQUESTED",
            payload={"quote_id": quote_id, "quote_version": quote_version},
        )

    def record_human_approval(
        self,
        *,
        approval_id: str,
        actor_type: ActorType,
        actor_id: str,
        quote_id: str,
        quote_version: int,
    ) -> None:
        self._require_state(
            {ProcurementRequestState.AWAITING_APPROVAL},
            ProcurementRequestState.APPROVED,
        )
        if actor_type is not ActorType.HUMAN:
            raise DomainError(
                ErrorCode.POLICY_DENIED,
                "Only a human approval can approve procurement spend",
                details={"actor_type": actor_type.value},
            )
        actor_id = require_non_empty(actor_id, field="actor_id")
        approval_id = require_prefixed_id(approval_id, field="approval_id", prefix="apr_")
        self._require_selected_quote(quote_id=quote_id, quote_version=quote_version)
        self.approval_id = approval_id
        self._transition(
            allowed_from={ProcurementRequestState.AWAITING_APPROVAL},
            new_state=ProcurementRequestState.APPROVED,
            event_type="APPROVAL_GRANTED",
            payload={
                "approval_id": approval_id,
                "actor_id": actor_id,
                "quote_id": quote_id,
                "quote_version": quote_version,
            },
        )

    def record_approval_rejected(self, *, approval_id: str, reason: str) -> None:
        self._require_state(
            {ProcurementRequestState.AWAITING_APPROVAL},
            ProcurementRequestState.APPROVAL_REJECTED,
        )
        approval_id = require_prefixed_id(approval_id, field="approval_id", prefix="apr_")
        reason = require_non_empty(reason, field="reason")
        self._transition(
            allowed_from={ProcurementRequestState.AWAITING_APPROVAL},
            new_state=ProcurementRequestState.APPROVAL_REJECTED,
            event_type="APPROVAL_REJECTED",
            payload={"approval_id": approval_id, "reason": reason},
        )

    def record_award_sent(
        self,
        *,
        award_id: str,
        delivery_ack_id: str,
        quote_id: str,
        quote_version: int,
        terms_hash: str,
    ) -> None:
        self._require_state({ProcurementRequestState.APPROVED}, ProcurementRequestState.AWARD_SENT)
        award_id = require_prefixed_id(award_id, field="award_id", prefix="awd_")
        delivery_ack_id = self._require_delivery_ack(delivery_ack_id)
        terms_hash = require_non_empty(terms_hash, field="terms_hash")
        self._require_selected_quote(quote_id=quote_id, quote_version=quote_version)
        self.award_id = award_id
        self.award_terms_hash = terms_hash
        self._transition(
            allowed_from={ProcurementRequestState.APPROVED},
            new_state=ProcurementRequestState.AWARD_SENT,
            event_type="AWARD_DELIVERY_CONFIRMED",
            payload={
                "award_id": award_id,
                "delivery_ack_id": delivery_ack_id,
                "quote_id": quote_id,
                "quote_version": quote_version,
                "terms_hash": terms_hash,
            },
        )

    def record_supplier_acceptance(
        self,
        *,
        award_id: str,
        submission_id: str,
        terms_hash: str,
    ) -> None:
        self._require_state(
            {ProcurementRequestState.AWARD_SENT},
            ProcurementRequestState.SUPPLIER_ACCEPTED,
        )
        submission_id = require_non_empty(submission_id, field="submission_id")
        self._require_matching_award(award_id=award_id, terms_hash=terms_hash)
        self.acceptance_submission_id = submission_id
        self._transition(
            allowed_from={ProcurementRequestState.AWARD_SENT},
            new_state=ProcurementRequestState.SUPPLIER_ACCEPTED,
            event_type="SUPPLIER_ACCEPTED_AWARD",
            payload={
                "award_id": award_id,
                "submission_id": submission_id,
                "terms_hash": terms_hash,
            },
        )

    def record_supplier_decline(self, *, award_id: str, submission_id: str) -> None:
        self._require_state(
            {ProcurementRequestState.AWARD_SENT},
            ProcurementRequestState.SUPPLIER_DECLINED_AWARD,
        )
        award_id = require_prefixed_id(award_id, field="award_id", prefix="awd_")
        submission_id = require_non_empty(submission_id, field="submission_id")
        if award_id != self.award_id:
            raise DomainError(
                ErrorCode.CONFLICT,
                "Supplier response references a different award",
                details={"expected_award_id": self.award_id, "award_id": award_id},
            )
        self._transition(
            allowed_from={ProcurementRequestState.AWARD_SENT},
            new_state=ProcurementRequestState.SUPPLIER_DECLINED_AWARD,
            event_type="SUPPLIER_DECLINED_AWARD",
            payload={"award_id": award_id, "submission_id": submission_id},
        )

    def mark_ready_for_contracting(
        self,
        *,
        reservation_id: str,
        confirmed_at: datetime,
    ) -> None:
        self._require_state(
            {ProcurementRequestState.SUPPLIER_ACCEPTED},
            ProcurementRequestState.READY_FOR_CONTRACTING,
        )
        reservation_id = require_non_empty(reservation_id, field="reservation_id")
        confirmed_at = require_utc(confirmed_at, field="confirmed_at")
        self.reservation_id = reservation_id
        self._transition(
            allowed_from={ProcurementRequestState.SUPPLIER_ACCEPTED},
            new_state=ProcurementRequestState.READY_FOR_CONTRACTING,
            event_type="PROCUREMENT_READY_FOR_CONTRACTING",
            payload={"reservation_id": reservation_id, "confirmed_at": confirmed_at.isoformat()},
        )

    def close(self) -> None:
        self._transition(
            allowed_from={ProcurementRequestState.READY_FOR_CONTRACTING},
            new_state=ProcurementRequestState.CLOSED,
            event_type="PROCUREMENT_CLOSED",
        )

    def cancel(self, *, actor_id: str, reason: str) -> None:
        self._require_state(_PRE_COMMIT_STATES, ProcurementRequestState.CANCELLED)
        actor_id = require_non_empty(actor_id, field="actor_id")
        reason = require_non_empty(reason, field="reason")
        self._transition(
            allowed_from=_PRE_COMMIT_STATES,
            new_state=ProcurementRequestState.CANCELLED,
            event_type="PROCUREMENT_CANCELLED",
            payload={"actor_id": actor_id, "reason": reason},
        )

    def expire(self, *, expired_at: datetime) -> None:
        self._require_state(_PRE_COMMIT_STATES, ProcurementRequestState.EXPIRED)
        expired_at = require_utc(expired_at, field="expired_at")
        self._transition(
            allowed_from=_PRE_COMMIT_STATES,
            new_state=ProcurementRequestState.EXPIRED,
            event_type="PROCUREMENT_EXPIRED",
            payload={"expired_at": expired_at.isoformat()},
        )

    def _require_selected_quote(self, *, quote_id: str, quote_version: int) -> None:
        quote_id = require_prefixed_id(quote_id, field="quote_id", prefix="quo_")
        quote_version = require_positive_int(quote_version, field="quote_version")
        if quote_id != self.selected_quote_id or quote_version != self.selected_quote_version:
            raise DomainError(
                ErrorCode.CONFLICT,
                "Action does not reference the selected quote version",
                details={
                    "selected_quote_id": self.selected_quote_id,
                    "selected_quote_version": self.selected_quote_version,
                    "quote_id": quote_id,
                    "quote_version": quote_version,
                },
            )

    def _require_matching_award(self, *, award_id: str, terms_hash: str) -> None:
        award_id = require_prefixed_id(award_id, field="award_id", prefix="awd_")
        terms_hash = require_non_empty(terms_hash, field="terms_hash")
        if (
            award_id != self.award_id
            or self.award_terms_hash is None
            or not hmac.compare_digest(
                terms_hash,
                self.award_terms_hash,
            )
        ):
            raise DomainError(
                ErrorCode.CONFLICT,
                "Supplier acceptance does not match the delivered award",
                details={"expected_award_id": self.award_id, "award_id": award_id},
            )

    @staticmethod
    def _require_delivery_ack(delivery_ack_id: str) -> str:
        if not isinstance(delivery_ack_id, str) or not delivery_ack_id.strip():
            raise DomainError(
                ErrorCode.EXTERNAL_DELIVERY_NOT_CONFIRMED,
                "State change requires a real delivery acknowledgement",
                details={"field": "delivery_ack_id"},
            )
        return delivery_ack_id
