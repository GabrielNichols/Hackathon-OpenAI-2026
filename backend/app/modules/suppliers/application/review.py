from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Protocol, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..extraction.models import ExtractedFieldDTO, ExtractionFieldStatus
from .core_compat import (
    ActivateSupplierCommand,
    AuditPort,
    Clock,
    IdGenerator,
    ReviewTokenClaims,
    ReviewTokenError,
    SignedReviewTokenService,
    SupplierActivationCommandPort,
    SupplierAuditEvent,
    SupplierLifecycleStatus,
    UuidIdGenerator,
)


class FieldReviewDecision(StrEnum):
    CONFIRMED = "confirmed"
    CORRECTED = "corrected"
    NOT_APPLICABLE = "not_applicable"


class FieldReviewRevision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    field_name: str
    version: int = Field(ge=1)
    decision: FieldReviewDecision | None
    value: Any | None
    normalized_value: Any | None
    extraction_status: ExtractionFieldStatus
    confidence: float | None = Field(default=None, ge=0, le=1)
    source_document_id: str
    source_page: int | None = Field(default=None, ge=1)
    source_sheet: str | None = None
    source_cell_range: str | None = None
    source_excerpt: str | None = None
    extraction_run_id: str
    decided_by: str | None = None
    decided_at: datetime | None = None

    @model_validator(mode="after")
    def decision_metadata_is_consistent(self) -> Self:
        if self.decision is None and (self.decided_by is not None or self.decided_at is not None):
            raise ValueError("extracted revision cannot contain a human decision")
        if self.decision is not None and (self.decided_by is None or self.decided_at is None):
            raise ValueError("human review decision requires actor and timestamp")
        if self.decision is FieldReviewDecision.NOT_APPLICABLE:
            if self.value is not None or self.normalized_value is not None:
                raise ValueError("not_applicable is a separate decision without a field value")
        return self

    @classmethod
    def from_extracted(cls, field: ExtractedFieldDTO) -> Self:
        return cls(
            field_name=field.field_name,
            version=field.version,
            decision=None,
            value=field.value,
            normalized_value=field.normalized_value,
            extraction_status=field.status,
            confidence=field.confidence,
            source_document_id=field.source_document_id,
            source_page=field.source_page,
            source_sheet=field.source_sheet,
            source_cell_range=field.source_cell_range,
            source_excerpt=field.source_excerpt,
            extraction_run_id=field.extraction_run_id,
        )

    def with_decision(
        self,
        *,
        decision: FieldReviewDecision,
        value: Any | None,
        normalized_value: Any | None,
        actor_id: str,
        decided_at: datetime,
    ) -> Self:
        return self.__class__(
            field_name=self.field_name,
            version=self.version + 1,
            decision=decision,
            value=value,
            normalized_value=normalized_value,
            extraction_status=self.extraction_status,
            confidence=self.confidence,
            source_document_id=self.source_document_id,
            source_page=self.source_page,
            source_sheet=self.source_sheet,
            source_cell_range=self.source_cell_range,
            source_excerpt=self.source_excerpt,
            extraction_run_id=self.extraction_run_id,
            decided_by=actor_id,
            decided_at=decided_at,
        )


class SupplierReviewSession(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    review_id: str
    tenant_id: str
    supplier_id: str
    recipient_id: str
    required_fields: tuple[str, ...]
    revisions: dict[str, list[FieldReviewRevision]]
    projected_status: SupplierLifecycleStatus = SupplierLifecycleStatus.AWAITING_SUPPLIER_REVIEW
    review_submitted_at: datetime | None = None
    review_submission_id: str | None = None
    aggregate_version: int | None = None
    version: int = Field(default=1, ge=1)

    @classmethod
    def from_extracted_fields(
        cls,
        *,
        review_id: str,
        tenant_id: str,
        supplier_id: str,
        recipient_id: str,
        required_fields: tuple[str, ...],
        fields: list[ExtractedFieldDTO],
    ) -> Self:
        field_names = [field.field_name for field in fields]
        if len(field_names) != len(set(field_names)):
            raise ValueError("review cannot contain duplicate field names")
        return cls(
            review_id=review_id,
            tenant_id=tenant_id,
            supplier_id=supplier_id,
            recipient_id=recipient_id,
            required_fields=required_fields,
            revisions={
                field.field_name: [FieldReviewRevision.from_extracted(field)] for field in fields
            },
        )

    def field_history(self, field_name: str) -> tuple[FieldReviewRevision, ...]:
        try:
            return tuple(self.revisions[field_name])
        except KeyError as error:
            raise ReviewFieldNotFoundError(field_name) from error

    def current_field(self, field_name: str) -> FieldReviewRevision:
        return self.field_history(field_name)[-1]

    def append_decision(
        self,
        *,
        field_name: str,
        decision: FieldReviewDecision,
        value: Any | None,
        normalized_value: Any | None,
        actor_id: str,
        decided_at: datetime,
        expected_version: int,
    ) -> FieldReviewRevision:
        if self.projected_status is SupplierLifecycleStatus.ACTIVE:
            raise ReviewConflictError("review was already activated", code="CONFLICT")
        current = self.current_field(field_name)
        if current.version != expected_version:
            raise ReviewConflictError(
                "stale supplier field version",
                code="OPTIMISTIC_LOCK_CONFLICT",
            )
        revision = current.with_decision(
            decision=decision,
            value=value,
            normalized_value=normalized_value,
            actor_id=actor_id,
            decided_at=decided_at,
        )
        self.revisions[field_name].append(revision)
        self.version += 1
        return revision

    def missing_required_fields(self) -> tuple[str, ...]:
        confirmed_decisions = {
            FieldReviewDecision.CONFIRMED,
            FieldReviewDecision.CORRECTED,
        }
        return tuple(
            field_name
            for field_name in self.required_fields
            if field_name not in self.revisions
            or self.current_field(field_name).decision not in confirmed_decisions
        )

    def confirmed_fields(self) -> tuple[str, ...]:
        accepted = {FieldReviewDecision.CONFIRMED, FieldReviewDecision.CORRECTED}
        return tuple(
            field_name
            for field_name in self.revisions
            if self.current_field(field_name).decision in accepted
        )


class SupplierReviewSubmissionResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    review_submission_id: str
    supplier_id: str
    status: SupplierLifecycleStatus
    activated_at: datetime
    aggregate_version: int


class ReviewError(Exception):
    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.message = message
        self.code = code


class ReviewFieldNotFoundError(ReviewError):
    def __init__(self, field_name: str) -> None:
        super().__init__(f"supplier review field not found: {field_name}", code="NOT_FOUND")
        self.field_name = field_name


class ReviewConflictError(ReviewError):
    pass


class ReviewIncompleteError(ReviewError):
    def __init__(self, missing_fields: tuple[str, ...]) -> None:
        super().__init__(
            "supplier review has missing required fields",
            code="REVIEW_INCOMPLETE",
        )
        self.missing_fields = missing_fields


class SupplierReviewRepository(Protocol):
    def add(self, session: SupplierReviewSession) -> None: ...

    def get(self, review_id: str) -> SupplierReviewSession: ...

    def find_for_recipient(
        self,
        *,
        tenant_id: str,
        supplier_id: str,
        recipient_id: str,
    ) -> SupplierReviewSession | None: ...


class InMemorySupplierReviewRepository:
    def __init__(self) -> None:
        self._sessions: dict[str, SupplierReviewSession] = {}

    def add(self, session: SupplierReviewSession) -> None:
        if session.review_id in self._sessions:
            raise ReviewConflictError("supplier review already exists", code="CONFLICT")
        self._sessions[session.review_id] = session

    def get(self, review_id: str) -> SupplierReviewSession:
        try:
            return self._sessions[review_id]
        except KeyError as error:
            raise ReviewError("supplier review not found", code="NOT_FOUND") from error

    def find_for_recipient(
        self,
        *,
        tenant_id: str,
        supplier_id: str,
        recipient_id: str,
    ) -> SupplierReviewSession | None:
        return next(
            (
                session
                for session in self._sessions.values()
                if session.tenant_id == tenant_id
                and session.supplier_id == supplier_id
                and session.recipient_id == recipient_id
            ),
            None,
        )


class SupplierReviewService:
    def __init__(
        self,
        *,
        repository: SupplierReviewRepository,
        token_service: SignedReviewTokenService,
        activation_port: SupplierActivationCommandPort,
        audit_port: AuditPort,
        clock: Clock,
        id_generator: IdGenerator | None = None,
    ) -> None:
        self._repository = repository
        self._token_service = token_service
        self._activation_port = activation_port
        self._audit_port = audit_port
        self._clock = clock
        self._ids = id_generator or UuidIdGenerator()

    async def get_review(self, token: str) -> SupplierReviewSession:
        _, session = self._resolve(token)
        return session

    async def confirm_field(
        self,
        token: str,
        field_name: str,
        *,
        expected_version: int,
    ) -> FieldReviewRevision:
        claims, session = self._resolve(token)
        current = session.current_field(field_name)
        revision = session.append_decision(
            field_name=field_name,
            decision=FieldReviewDecision.CONFIRMED,
            value=current.value,
            normalized_value=current.normalized_value,
            actor_id=claims.recipient_id,
            decided_at=self._clock.now(),
            expected_version=expected_version,
        )
        await self._audit_field_decision(session, revision, claims)
        return revision

    async def correct_field(
        self,
        token: str,
        field_name: str,
        *,
        value: Any,
        normalized_value: Any,
        expected_version: int,
    ) -> FieldReviewRevision:
        if value is None and normalized_value is None:
            raise ReviewConflictError(
                "use not_applicable for an explicit empty human decision",
                code="VALIDATION_ERROR",
            )
        claims, session = self._resolve(token)
        revision = session.append_decision(
            field_name=field_name,
            decision=FieldReviewDecision.CORRECTED,
            value=value,
            normalized_value=normalized_value,
            actor_id=claims.recipient_id,
            decided_at=self._clock.now(),
            expected_version=expected_version,
        )
        await self._audit_field_decision(session, revision, claims)
        return revision

    async def mark_not_applicable(
        self,
        token: str,
        field_name: str,
        *,
        expected_version: int,
    ) -> FieldReviewRevision:
        claims, session = self._resolve(token)
        revision = session.append_decision(
            field_name=field_name,
            decision=FieldReviewDecision.NOT_APPLICABLE,
            value=None,
            normalized_value=None,
            actor_id=claims.recipient_id,
            decided_at=self._clock.now(),
            expected_version=expected_version,
        )
        await self._audit_field_decision(session, revision, claims)
        return revision

    async def submit(self, token: str) -> SupplierReviewSubmissionResult:
        claims, session = self._resolve(token)
        missing_fields = session.missing_required_fields()
        if missing_fields:
            await self._audit_activation_blocked(session, claims, missing_fields)
            raise ReviewIncompleteError(missing_fields)

        claims = self._token_service.consume_for_submit(
            token,
            expected_tenant_id=session.tenant_id,
            expected_supplier_id=session.supplier_id,
            expected_recipient_id=session.recipient_id,
        )
        correlation_id = self._ids.new("cor")
        submission_id = self._ids.new("subrev")
        previous_state = session.projected_status
        session.projected_status = SupplierLifecycleStatus.CONFIRMED
        session.review_submitted_at = self._clock.now()
        session.review_submission_id = submission_id
        session.version += 1
        await self._audit_port.append(
            [
                self._event(
                    event_type="SUPPLIER_REVIEW_SUBMITTED",
                    session=session,
                    actor_type="supplier",
                    actor_id=claims.recipient_id,
                    correlation_id=correlation_id,
                    previous_state=previous_state,
                    new_state=SupplierLifecycleStatus.CONFIRMED,
                    idempotency_key=f"supplier-review-submit:{claims.nonce}",
                    payload={"review_submission_id": submission_id},
                )
            ]
        )

        command = ActivateSupplierCommand(
            tenant_id=session.tenant_id,
            supplier_id=session.supplier_id,
            review_submission_id=submission_id,
            required_fields=session.required_fields,
            confirmed_fields=session.confirmed_fields(),
            idempotency_key=f"supplier-review-submit:{claims.nonce}",
            correlation_id=correlation_id,
        )
        try:
            activation = await self._activation_port.activate(command)
        except Exception:
            await self._audit_activation_blocked(session, claims, (), correlation_id=correlation_id)
            raise
        if (
            activation.supplier_id != session.supplier_id
            or activation.status is not SupplierLifecycleStatus.ACTIVE
        ):
            await self._audit_activation_blocked(session, claims, (), correlation_id=correlation_id)
            raise ReviewConflictError(
                "core rejected supplier activation", code="INVALID_STATE_TRANSITION"
            )

        session.projected_status = activation.status
        session.aggregate_version = activation.version
        session.version += 1
        await self._audit_port.append(
            [
                self._event(
                    event_type="SUPPLIER_ACTIVATED",
                    session=session,
                    actor_type="system",
                    actor_id=None,
                    correlation_id=correlation_id,
                    previous_state=SupplierLifecycleStatus.CONFIRMED,
                    new_state=SupplierLifecycleStatus.ACTIVE,
                    idempotency_key=command.idempotency_key,
                    payload={
                        "review_submission_id": submission_id,
                        "aggregate_version": activation.version,
                    },
                )
            ]
        )
        return SupplierReviewSubmissionResult(
            review_submission_id=submission_id,
            supplier_id=session.supplier_id,
            status=activation.status,
            activated_at=activation.activated_at,
            aggregate_version=activation.version,
        )

    def _resolve(self, token: str) -> tuple[ReviewTokenClaims, SupplierReviewSession]:
        claims = self._token_service.validate(token)
        session = self._repository.find_for_recipient(
            tenant_id=claims.tenant_id,
            supplier_id=claims.supplier_id,
            recipient_id=claims.recipient_id,
        )
        if session is None:
            raise ReviewTokenError("LINK_INVALID", "invalid review link binding")
        return claims, session

    async def _audit_field_decision(
        self,
        session: SupplierReviewSession,
        revision: FieldReviewRevision,
        claims: ReviewTokenClaims,
    ) -> None:
        if revision.decision is None:
            raise ValueError("audit requires a supplier field decision")
        event_type = {
            FieldReviewDecision.CONFIRMED: "SUPPLIER_FIELD_CONFIRMED",
            FieldReviewDecision.CORRECTED: "SUPPLIER_FIELD_CORRECTED",
            FieldReviewDecision.NOT_APPLICABLE: "SUPPLIER_FIELD_CORRECTED",
        }[revision.decision]
        await self._audit_port.append(
            [
                self._event(
                    event_type=event_type,
                    session=session,
                    actor_type="supplier",
                    actor_id=claims.recipient_id,
                    correlation_id=self._ids.new("cor"),
                    payload={
                        "field_name": revision.field_name,
                        "field_version": revision.version,
                        "decision": revision.decision,
                        "source_document_id": revision.source_document_id,
                    },
                )
            ]
        )

    async def _audit_activation_blocked(
        self,
        session: SupplierReviewSession,
        claims: ReviewTokenClaims,
        missing_fields: tuple[str, ...],
        *,
        correlation_id: str | None = None,
    ) -> None:
        await self._audit_port.append(
            [
                self._event(
                    event_type="SUPPLIER_ACTIVATION_BLOCKED",
                    session=session,
                    actor_type="supplier",
                    actor_id=claims.recipient_id,
                    correlation_id=correlation_id or self._ids.new("cor"),
                    previous_state=session.projected_status,
                    new_state=session.projected_status,
                    payload={"missing_fields": list(missing_fields)},
                )
            ]
        )

    def _event(
        self,
        *,
        event_type: str,
        session: SupplierReviewSession,
        actor_type: str,
        actor_id: str | None,
        correlation_id: str,
        payload: dict[str, Any],
        previous_state: SupplierLifecycleStatus | None = None,
        new_state: SupplierLifecycleStatus | None = None,
        idempotency_key: str | None = None,
    ) -> SupplierAuditEvent:
        return SupplierAuditEvent(
            event_id=self._ids.new("evt"),
            event_type=event_type,
            aggregate_id=session.supplier_id,
            actor_type=actor_type,
            actor_id=actor_id,
            occurred_at=self._clock.now(),
            previous_state=previous_state,
            new_state=new_state,
            correlation_id=correlation_id,
            idempotency_key=idempotency_key,
            payload=payload,
        )
