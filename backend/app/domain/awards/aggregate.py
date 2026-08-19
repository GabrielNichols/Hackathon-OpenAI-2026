"""Award delivery and supplier acceptance lifecycle."""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Mapping
from datetime import datetime
from typing import Any, ClassVar

from app.contracts import AwardStatus, ErrorCode
from app.domain.approvals import ApprovalAggregate
from app.domain.common import (
    AggregateRoot,
    DomainError,
    require_non_empty,
    require_prefixed_id,
    require_utc,
)


class AwardAggregate(AggregateRoot[AwardStatus]):
    aggregate_type: ClassVar[str] = "award"

    def __init__(
        self,
        *,
        award_id: str,
        tenant_id: str,
        procurement_request_id: str,
        supplier_id: str,
        approval: ApprovalAggregate,
        terms_snapshot: Mapping[str, Any],
    ) -> None:
        procurement_request_id = require_prefixed_id(
            procurement_request_id,
            field="procurement_request_id",
            prefix="pr_",
        )
        self._validate_approval_scope(
            approval=approval,
            tenant_id=tenant_id,
            procurement_request_id=procurement_request_id,
        )
        approval.assert_valid_for(
            quote_id=approval.quote_id,
            quote_version=approval.quote_version,
        )
        snapshot, terms_hash = self._canonical_snapshot(terms_snapshot)

        super().__init__(
            aggregate_id=require_prefixed_id(
                award_id,
                field="award_id",
                prefix="awd_",
            ),
            tenant_id=tenant_id,
            state=AwardStatus.CREATED,
            created_event_type="AWARD_CREATED",
        )
        self.procurement_request_id = procurement_request_id
        self.supplier_id = require_prefixed_id(
            supplier_id,
            field="supplier_id",
            prefix="sup_",
        )
        self.approval_id = approval.id
        self.approved_quote_id = approval.quote_id
        self.approved_quote_version = approval.quote_version
        self.approved_total_cents = approval.approved_total.cents
        self.terms_snapshot = snapshot
        self.terms_hash = terms_hash
        self.delivery_ack_id: str | None = None
        self.sent_at: datetime | None = None
        self.response_submission_id: str | None = None
        self.respondent_id: str | None = None
        self.responded_at: datetime | None = None

    @classmethod
    def create(
        cls,
        *,
        award_id: str,
        tenant_id: str,
        procurement_request_id: str,
        supplier_id: str,
        approval: ApprovalAggregate,
        terms_snapshot: Mapping[str, Any],
    ) -> AwardAggregate:
        return cls(
            award_id=award_id,
            tenant_id=tenant_id,
            procurement_request_id=procurement_request_id,
            supplier_id=supplier_id,
            approval=approval,
            terms_snapshot=terms_snapshot,
        )

    def confirm_delivery(self, *, delivery_ack_id: str, sent_at: datetime) -> None:
        self._require_state({AwardStatus.CREATED}, AwardStatus.SENT)
        if not isinstance(delivery_ack_id, str) or not delivery_ack_id.strip():
            raise DomainError(
                ErrorCode.EXTERNAL_DELIVERY_NOT_CONFIRMED,
                "Award cannot be sent without a delivery acknowledgement",
                details={"field": "delivery_ack_id"},
            )
        sent_at = require_utc(sent_at, field="sent_at")
        self.delivery_ack_id = delivery_ack_id
        self.sent_at = sent_at
        self._transition(
            allowed_from={AwardStatus.CREATED},
            new_state=AwardStatus.SENT,
            event_type="AWARD_DELIVERY_CONFIRMED",
            payload={"delivery_ack_id": delivery_ack_id, "sent_at": sent_at.isoformat()},
        )

    def accept(
        self,
        *,
        submission_id: str,
        respondent_id: str,
        submitted_at: datetime,
        displayed_terms_hash: str,
        explicit_confirmation: bool,
    ) -> None:
        self._require_state({AwardStatus.SENT}, AwardStatus.ACCEPTED)
        submission_id, respondent_id, submitted_at = self._validated_response(
            submission_id=submission_id,
            respondent_id=respondent_id,
            submitted_at=submitted_at,
        )
        if explicit_confirmation is not True:
            raise DomainError(
                ErrorCode.VALIDATION_ERROR,
                "Award acceptance requires explicit confirmation",
                details={"field": "explicit_confirmation"},
            )
        if not isinstance(displayed_terms_hash, str) or not hmac.compare_digest(
            displayed_terms_hash,
            self.terms_hash,
        ):
            raise DomainError(
                ErrorCode.LINK_INVALID,
                "Accepted terms do not match the awarded snapshot",
                details={"field": "displayed_terms_hash"},
            )
        self._record_response(submission_id, respondent_id, submitted_at)
        self._transition(
            allowed_from={AwardStatus.SENT},
            new_state=AwardStatus.ACCEPTED,
            event_type="SUPPLIER_ACCEPTED_AWARD",
            payload={
                "submission_id": submission_id,
                "respondent_id": respondent_id,
                "submitted_at": submitted_at.isoformat(),
                "terms_hash": self.terms_hash,
            },
        )

    def decline(
        self,
        *,
        submission_id: str,
        respondent_id: str,
        submitted_at: datetime,
    ) -> None:
        self._require_state({AwardStatus.SENT}, AwardStatus.DECLINED)
        submission_id, respondent_id, submitted_at = self._validated_response(
            submission_id=submission_id,
            respondent_id=respondent_id,
            submitted_at=submitted_at,
        )
        self._record_response(submission_id, respondent_id, submitted_at)
        self._transition(
            allowed_from={AwardStatus.SENT},
            new_state=AwardStatus.DECLINED,
            event_type="SUPPLIER_DECLINED_AWARD",
            payload={
                "submission_id": submission_id,
                "respondent_id": respondent_id,
                "submitted_at": submitted_at.isoformat(),
            },
        )

    def _record_response(
        self, submission_id: str, respondent_id: str, submitted_at: datetime
    ) -> None:
        self.response_submission_id = submission_id
        self.respondent_id = respondent_id
        self.responded_at = submitted_at

    @staticmethod
    def _validated_response(
        *,
        submission_id: str,
        respondent_id: str,
        submitted_at: datetime,
    ) -> tuple[str, str, datetime]:
        return (
            require_non_empty(submission_id, field="submission_id"),
            require_non_empty(respondent_id, field="respondent_id"),
            require_utc(submitted_at, field="submitted_at"),
        )

    @staticmethod
    def _validate_approval_scope(
        *,
        approval: ApprovalAggregate,
        tenant_id: str,
        procurement_request_id: str,
    ) -> None:
        if (
            approval.tenant_id != tenant_id
            or approval.procurement_request_id != procurement_request_id
        ):
            raise DomainError(
                ErrorCode.POLICY_DENIED,
                "Approval and award must share tenant and procurement request",
                details={"approval_id": approval.id},
            )

    @staticmethod
    def _canonical_snapshot(terms_snapshot: Mapping[str, Any]) -> tuple[dict[str, Any], str]:
        if not terms_snapshot:
            raise DomainError(
                ErrorCode.VALIDATION_ERROR,
                "Award terms snapshot is required",
                details={"field": "terms_snapshot"},
            )
        try:
            canonical = json.dumps(
                dict(terms_snapshot),
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
            snapshot = json.loads(canonical)
        except (TypeError, ValueError) as exc:
            raise DomainError(
                ErrorCode.VALIDATION_ERROR,
                "Award terms snapshot must be JSON serializable",
                details={"field": "terms_snapshot"},
            ) from exc
        return snapshot, hashlib.sha256(canonical.encode("utf-8")).hexdigest()
