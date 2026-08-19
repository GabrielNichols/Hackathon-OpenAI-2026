"""Immutable human decision bound to an exact quote version."""

from __future__ import annotations

from datetime import datetime
from typing import ClassVar

from app.contracts import ActorType, ApprovalStatus, ErrorCode
from app.domain.common import (
    AggregateRoot,
    DomainError,
    Money,
    require_non_empty,
    require_positive_int,
    require_prefixed_id,
    require_utc,
)


class ApprovalAggregate(AggregateRoot[ApprovalStatus]):
    aggregate_type: ClassVar[str] = "approval"

    def __init__(
        self,
        *,
        approval_id: str,
        tenant_id: str,
        procurement_request_id: str,
        quote_id: str,
        quote_version: int,
        approved_total_cents: int,
    ) -> None:
        super().__init__(
            aggregate_id=require_prefixed_id(
                approval_id,
                field="approval_id",
                prefix="apr_",
            ),
            tenant_id=tenant_id,
            state=ApprovalStatus.REQUESTED,
            created_event_type="APPROVAL_REQUESTED",
        )
        self.procurement_request_id = require_prefixed_id(
            procurement_request_id,
            field="procurement_request_id",
            prefix="pr_",
        )
        self.quote_id = require_prefixed_id(quote_id, field="quote_id", prefix="quo_")
        self.quote_version = require_positive_int(quote_version, field="quote_version")
        self.approved_total = Money(approved_total_cents)
        self.decided_by: str | None = None
        self.decided_at: datetime | None = None
        self.reason: str | None = None

    @classmethod
    def create(
        cls,
        *,
        approval_id: str,
        tenant_id: str,
        procurement_request_id: str,
        quote_id: str,
        quote_version: int,
        approved_total_cents: int,
    ) -> ApprovalAggregate:
        return cls(
            approval_id=approval_id,
            tenant_id=tenant_id,
            procurement_request_id=procurement_request_id,
            quote_id=quote_id,
            quote_version=quote_version,
            approved_total_cents=approved_total_cents,
        )

    def approve(self, *, actor_type: ActorType, actor_id: str, decided_at: datetime) -> None:
        self._require_state({ApprovalStatus.REQUESTED}, ApprovalStatus.APPROVED)
        actor_id, decided_at = self._validated_human_decision(actor_type, actor_id, decided_at)
        self.decided_by = actor_id
        self.decided_at = decided_at
        self._transition(
            allowed_from={ApprovalStatus.REQUESTED},
            new_state=ApprovalStatus.APPROVED,
            event_type="APPROVAL_GRANTED",
            payload={
                "actor_id": actor_id,
                "quote_id": self.quote_id,
                "quote_version": self.quote_version,
                "decided_at": decided_at.isoformat(),
            },
        )

    def reject(
        self,
        *,
        actor_type: ActorType,
        actor_id: str,
        decided_at: datetime,
        reason: str,
    ) -> None:
        self._require_state({ApprovalStatus.REQUESTED}, ApprovalStatus.REJECTED)
        actor_id, decided_at = self._validated_human_decision(actor_type, actor_id, decided_at)
        reason = require_non_empty(reason, field="reason")
        self.decided_by = actor_id
        self.decided_at = decided_at
        self.reason = reason
        self._transition(
            allowed_from={ApprovalStatus.REQUESTED},
            new_state=ApprovalStatus.REJECTED,
            event_type="APPROVAL_REJECTED",
            payload={"actor_id": actor_id, "reason": reason, "decided_at": decided_at.isoformat()},
        )

    def request_changes(
        self,
        *,
        actor_type: ActorType,
        actor_id: str,
        decided_at: datetime,
        reason: str,
    ) -> None:
        self._require_state({ApprovalStatus.REQUESTED}, ApprovalStatus.CHANGES_REQUESTED)
        actor_id, decided_at = self._validated_human_decision(actor_type, actor_id, decided_at)
        reason = require_non_empty(reason, field="reason")
        self.decided_by = actor_id
        self.decided_at = decided_at
        self.reason = reason
        self._transition(
            allowed_from={ApprovalStatus.REQUESTED},
            new_state=ApprovalStatus.CHANGES_REQUESTED,
            event_type="APPROVAL_CHANGES_REQUESTED",
            payload={"actor_id": actor_id, "reason": reason, "decided_at": decided_at.isoformat()},
        )

    def assert_valid_for(self, *, quote_id: str, quote_version: int) -> None:
        if self.state is not ApprovalStatus.APPROVED:
            raise DomainError(
                ErrorCode.POLICY_DENIED,
                "Award requires an approved human decision",
                details={"approval_id": self.id, "approval_state": self.state.value},
            )
        if quote_id != self.quote_id or quote_version != self.quote_version:
            raise DomainError(
                ErrorCode.CONFLICT,
                "Approval is bound to a different quote version",
                details={
                    "approved_quote_id": self.quote_id,
                    "approved_quote_version": self.quote_version,
                    "requested_quote_id": quote_id,
                    "requested_quote_version": quote_version,
                },
            )

    @staticmethod
    def _validated_human_decision(
        actor_type: ActorType,
        actor_id: str,
        decided_at: datetime,
    ) -> tuple[str, datetime]:
        if actor_type is not ActorType.HUMAN:
            raise DomainError(
                ErrorCode.POLICY_DENIED,
                "Only a human actor may decide an approval",
                details={"actor_type": actor_type.value},
            )
        return (
            require_non_empty(actor_id, field="actor_id"),
            require_utc(decided_at, field="decided_at"),
        )
