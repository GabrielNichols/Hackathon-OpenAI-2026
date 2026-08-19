"""Supplier profile lifecycle and activation invariants."""

from __future__ import annotations

from datetime import datetime
from typing import ClassVar

from app.contracts import ErrorCode, SupplierState
from app.domain.common import (
    AggregateRoot,
    DomainError,
    require_non_empty,
    require_prefixed_id,
    require_utc,
)

REQUIRED_SUPPLIER_FIELDS = frozenset(
    {
        "commercial_identity",
        "contact",
        "category",
        "service_region",
        "minimum_quantity",
        "approximate_capacity",
        "minimum_lead_time",
        "invoice_issuance",
        "supported_restrictions",
        "pricing_method",
        "updated_at",
    },
)


class SupplierAggregate(AggregateRoot[SupplierState]):
    aggregate_type: ClassVar[str] = "supplier"

    def __init__(self, *, supplier_id: str, tenant_id: str) -> None:
        super().__init__(
            aggregate_id=require_prefixed_id(
                supplier_id,
                field="supplier_id",
                prefix="sup_",
            ),
            tenant_id=tenant_id,
            state=SupplierState.DRAFT,
            created_event_type="SUPPLIER_CREATED",
        )
        self.document_id: str | None = None
        self.extraction_id: str | None = None
        self.review_id: str | None = None
        self.review_submission_id: str | None = None
        self.confirmed_fields: frozenset[str] = frozenset()

    @classmethod
    def create(cls, *, supplier_id: str, tenant_id: str) -> SupplierAggregate:
        return cls(supplier_id=supplier_id, tenant_id=tenant_id)

    def record_materials_uploaded(self, *, document_id: str) -> None:
        self._require_state({SupplierState.DRAFT}, SupplierState.MATERIALS_UPLOADED)
        document_id = require_non_empty(document_id, field="document_id")
        self.document_id = document_id
        self._transition(
            allowed_from={SupplierState.DRAFT},
            new_state=SupplierState.MATERIALS_UPLOADED,
            event_type="SOURCE_DOCUMENT_STORED",
            payload={"document_id": document_id},
        )

    def record_extraction(self, *, extraction_id: str) -> None:
        self._require_state({SupplierState.MATERIALS_UPLOADED}, SupplierState.EXTRACTED)
        extraction_id = require_non_empty(extraction_id, field="extraction_id")
        self.extraction_id = extraction_id
        self._transition(
            allowed_from={SupplierState.MATERIALS_UPLOADED},
            new_state=SupplierState.EXTRACTED,
            event_type="SUPPLIER_EXTRACTION_COMPLETED",
            payload={"extraction_id": extraction_id},
        )

    def request_review(self, *, review_id: str) -> None:
        self._require_state({SupplierState.EXTRACTED}, SupplierState.AWAITING_SUPPLIER_REVIEW)
        review_id = require_non_empty(review_id, field="review_id")
        self.review_id = review_id
        self._transition(
            allowed_from={SupplierState.EXTRACTED},
            new_state=SupplierState.AWAITING_SUPPLIER_REVIEW,
            event_type="SUPPLIER_REVIEW_LINK_CREATED",
            payload={"review_id": review_id},
        )

    def confirm_review(
        self,
        *,
        submission_id: str,
        confirmed_fields: frozenset[str],
    ) -> None:
        self._require_state(
            {SupplierState.AWAITING_SUPPLIER_REVIEW},
            SupplierState.CONFIRMED,
        )
        submission_id = require_non_empty(submission_id, field="submission_id")
        self.review_submission_id = submission_id
        self.confirmed_fields = frozenset(confirmed_fields)
        self._transition(
            allowed_from={SupplierState.AWAITING_SUPPLIER_REVIEW},
            new_state=SupplierState.CONFIRMED,
            event_type="SUPPLIER_REVIEW_SUBMITTED",
            payload={
                "submission_id": submission_id,
                "confirmed_fields": sorted(self.confirmed_fields),
            },
        )

    def activate(self) -> None:
        self._require_state({SupplierState.CONFIRMED}, SupplierState.ACTIVE)
        missing_fields = sorted(REQUIRED_SUPPLIER_FIELDS - self.confirmed_fields)
        if missing_fields:
            raise DomainError(
                ErrorCode.VALIDATION_ERROR,
                "Supplier required fields must be confirmed before activation",
                details={"missing_fields": missing_fields},
            )
        self._transition(
            allowed_from={SupplierState.CONFIRMED},
            new_state=SupplierState.ACTIVE,
            event_type="SUPPLIER_ACTIVATED",
        )

    def suspend(self, *, reason: str) -> None:
        self._require_state({SupplierState.ACTIVE}, SupplierState.SUSPENDED)
        reason = require_non_empty(reason, field="reason")
        self._transition(
            allowed_from={SupplierState.ACTIVE},
            new_state=SupplierState.SUSPENDED,
            event_type="SUPPLIER_SUSPENDED",
            payload={"reason": reason},
        )

    def expire(self, *, expired_at: datetime) -> None:
        self._require_state({SupplierState.ACTIVE}, SupplierState.EXPIRED)
        expired_at = require_utc(expired_at, field="expired_at")
        self._transition(
            allowed_from={SupplierState.ACTIVE},
            new_state=SupplierState.EXPIRED,
            event_type="SUPPLIER_PROFILE_EXPIRED",
            payload={"expired_at": expired_at.isoformat()},
        )
