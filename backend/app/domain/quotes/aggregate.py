"""Quote response, validation, negotiation, and decision lifecycle."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import ClassVar

from app.contracts import ErrorCode, QuoteState
from app.domain.common import (
    AggregateRoot,
    DomainError,
    Money,
    require_non_empty,
    require_prefixed_id,
    require_utc,
)


@dataclass(frozen=True, slots=True)
class QuoteValidationFacts:
    total: Money
    availability: bool
    items: tuple[str, ...]
    valid_until: datetime
    requirements: dict[str, str]
    respondent_id: str
    validated_at: datetime


class QuoteAggregate(AggregateRoot[QuoteState]):
    aggregate_type: ClassVar[str] = "quote"

    def __init__(
        self,
        *,
        quote_id: str,
        tenant_id: str,
        procurement_request_id: str,
        supplier_id: str,
        rfq_round_id: str,
    ) -> None:
        super().__init__(
            aggregate_id=require_prefixed_id(quote_id, field="quote_id", prefix="quo_"),
            tenant_id=tenant_id,
            state=QuoteState.REQUESTED,
            created_event_type="QUOTE_REQUESTED",
        )
        self.procurement_request_id = require_prefixed_id(
            procurement_request_id,
            field="procurement_request_id",
            prefix="pr_",
        )
        self.supplier_id = require_prefixed_id(
            supplier_id,
            field="supplier_id",
            prefix="sup_",
        )
        self.rfq_round_id = require_prefixed_id(
            rfq_round_id,
            field="rfq_round_id",
            prefix="rfq_",
        )
        self.open_event_id: str | None = None
        self.submission_id: str | None = None
        self.respondent_id: str | None = None
        self.validation_facts: QuoteValidationFacts | None = None

    @classmethod
    def create(
        cls,
        *,
        quote_id: str,
        tenant_id: str,
        procurement_request_id: str,
        supplier_id: str,
        rfq_round_id: str,
    ) -> QuoteAggregate:
        return cls(
            quote_id=quote_id,
            tenant_id=tenant_id,
            procurement_request_id=procurement_request_id,
            supplier_id=supplier_id,
            rfq_round_id=rfq_round_id,
        )

    def record_opened(self, *, open_event_id: str) -> None:
        self._require_state({QuoteState.REQUESTED}, QuoteState.OPENED)
        open_event_id = require_non_empty(open_event_id, field="open_event_id")
        self.open_event_id = open_event_id
        self._transition(
            allowed_from={QuoteState.REQUESTED},
            new_state=QuoteState.OPENED,
            event_type="RFQ_OPENED",
            payload={"open_event_id": open_event_id},
        )

    def save_draft(self) -> None:
        self._transition(
            allowed_from={QuoteState.OPENED},
            new_state=QuoteState.DRAFT_RESPONSE,
            event_type="QUOTE_DRAFT_SAVED",
        )

    def submit(self, *, submission_id: str, respondent_id: str) -> None:
        self._require_state({QuoteState.DRAFT_RESPONSE}, QuoteState.SUBMITTED)
        submission_id = require_non_empty(submission_id, field="submission_id")
        respondent_id = require_non_empty(respondent_id, field="respondent_id")
        self.submission_id = submission_id
        self.respondent_id = respondent_id
        self._transition(
            allowed_from={QuoteState.DRAFT_RESPONSE},
            new_state=QuoteState.SUBMITTED,
            event_type="QUOTE_SUBMITTED",
            payload={"submission_id": submission_id, "respondent_id": respondent_id},
        )

    def start_validation(self) -> None:
        self._transition(
            allowed_from={QuoteState.SUBMITTED},
            new_state=QuoteState.VALIDATING,
            event_type="QUOTE_VALIDATION_STARTED",
        )

    def request_clarification(self, *, reason: str) -> None:
        self._require_state({QuoteState.VALIDATING}, QuoteState.NEEDS_CLARIFICATION)
        reason = require_non_empty(reason, field="reason")
        self._transition(
            allowed_from={QuoteState.VALIDATING},
            new_state=QuoteState.NEEDS_CLARIFICATION,
            event_type="QUOTE_NEEDS_CLARIFICATION",
            payload={"reason": reason},
        )

    def record_clarification(self, *, submission_id: str) -> None:
        self._require_state({QuoteState.NEEDS_CLARIFICATION}, QuoteState.VALIDATING)
        submission_id = require_non_empty(submission_id, field="submission_id")
        self.submission_id = submission_id
        self._transition(
            allowed_from={QuoteState.NEEDS_CLARIFICATION},
            new_state=QuoteState.VALIDATING,
            event_type="QUOTE_CLARIFICATION_SUBMITTED",
            payload={"submission_id": submission_id},
        )

    def mark_valid(
        self,
        *,
        total_cents: int,
        availability: bool,
        items: Sequence[str],
        valid_until: datetime,
        requirements: Mapping[str, str],
        respondent_id: str,
        validated_at: datetime,
    ) -> None:
        self._require_state({QuoteState.VALIDATING}, QuoteState.VALID)
        facts = self._validated_facts(
            total_cents=total_cents,
            availability=availability,
            items=items,
            valid_until=valid_until,
            requirements=requirements,
            respondent_id=respondent_id,
            validated_at=validated_at,
        )
        self.validation_facts = facts
        self._transition(
            allowed_from={QuoteState.VALIDATING},
            new_state=QuoteState.VALID,
            event_type="QUOTE_VALIDATED",
            payload={
                "total_cents": facts.total.cents,
                "availability": facts.availability,
                "valid_until": facts.valid_until.isoformat(),
                "respondent_id": facts.respondent_id,
            },
        )

    def start_negotiation(self) -> None:
        self._transition(
            allowed_from={QuoteState.VALID},
            new_state=QuoteState.NEGOTIATING,
            event_type="NEGOTIATION_ROUND_CREATED",
        )

    def finalize(self) -> None:
        self._transition(
            allowed_from={QuoteState.VALID, QuoteState.NEGOTIATING},
            new_state=QuoteState.FINAL,
            event_type="QUOTE_FINALIZED",
        )

    def select(self) -> None:
        self._transition(
            allowed_from={QuoteState.FINAL},
            new_state=QuoteState.SELECTED,
            event_type="QUOTE_SELECTED",
        )

    def reject(self) -> None:
        self._transition(
            allowed_from={QuoteState.FINAL},
            new_state=QuoteState.REJECTED,
            event_type="QUOTE_REJECTED",
        )

    def expire(self, *, expired_at: datetime) -> None:
        self._require_state({QuoteState.FINAL}, QuoteState.EXPIRED)
        expired_at = require_utc(expired_at, field="expired_at")
        self._transition(
            allowed_from={QuoteState.FINAL},
            new_state=QuoteState.EXPIRED,
            event_type="QUOTE_EXPIRED",
            payload={"expired_at": expired_at.isoformat()},
        )

    def _validated_facts(
        self,
        *,
        total_cents: int,
        availability: bool,
        items: Sequence[str],
        valid_until: datetime,
        requirements: Mapping[str, str],
        respondent_id: str,
        validated_at: datetime,
    ) -> QuoteValidationFacts:
        total = Money(total_cents)
        if type(availability) is not bool:
            raise DomainError(
                ErrorCode.VALIDATION_ERROR,
                "Quote availability must be explicitly provided",
                details={"field": "availability"},
            )
        normalized_items = tuple(require_non_empty(item, field="items") for item in (items or ()))
        if not normalized_items:
            raise DomainError(
                ErrorCode.VALIDATION_ERROR,
                "Quote must include at least one item",
                details={"field": "items"},
            )
        if not requirements:
            raise DomainError(
                ErrorCode.VALIDATION_ERROR,
                "Quote requirement outcomes are required",
                details={"field": "requirements"},
            )
        normalized_requirements = {
            require_non_empty(key, field="requirements"): require_non_empty(
                value,
                field="requirements",
            )
            for key, value in requirements.items()
        }
        respondent_id = require_non_empty(respondent_id, field="respondent_id")
        if self.respondent_id is not None and respondent_id != self.respondent_id:
            raise DomainError(
                ErrorCode.CONFLICT,
                "Validated quote respondent differs from the real submission",
                details={
                    "submitted_respondent_id": self.respondent_id,
                    "validated_respondent_id": respondent_id,
                },
            )
        validated_at = require_utc(validated_at, field="validated_at")
        valid_until = require_utc(valid_until, field="valid_until")
        if valid_until <= validated_at:
            raise DomainError(
                ErrorCode.VALIDATION_ERROR,
                "Quote validity must be in the future",
                details={"field": "valid_until"},
            )
        return QuoteValidationFacts(
            total=total,
            availability=availability,
            items=normalized_items,
            valid_until=valid_until,
            requirements=normalized_requirements,
            respondent_id=respondent_id,
            validated_at=validated_at,
        )
