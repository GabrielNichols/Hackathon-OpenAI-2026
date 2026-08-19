"""Tenant-scoped repositories sharing a caller-owned SQLAlchemy transaction."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import UTC, datetime
from typing import Literal, cast

from sqlalchemy import Select, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.contracts import AuditEventDTO

from .errors import (
    IdempotencyConflict,
    IdempotencyInProgress,
    OptimisticLockConflict,
    RecordNotFound,
    TenantScopeViolation,
)
from .models import (
    AggregateRecordModel,
    AuditEventModel,
    ConsumedLinkNonceModel,
    IdempotencyRecordModel,
)
from .records import (
    AggregateRecord,
    AuditEventRecord,
    ConsumedLinkNonce,
    IdempotencyClaim,
    JsonObject,
    JsonValue,
)

FORBIDDEN_AUDIT_KEYS = frozenset(
    {
        "access_token",
        "authorization",
        "nonce",
        "password",
        "refresh_token",
        "secret",
        "signed_link",
        "token",
    }
)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamps must be timezone-aware")
    return value.astimezone(UTC)


def canonical_json_object(payload: Mapping[str, object]) -> JsonObject:
    """Normalize JSON and reject non-finite or non-serializable request data."""

    encoded = json.dumps(
        payload,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    decoded = json.loads(encoded)
    if not isinstance(decoded, dict):  # pragma: no cover - Mapping always encodes as an object
        raise TypeError("payload must encode to a JSON object")
    return cast(JsonObject, decoded)


def fingerprint_payload(payload: Mapping[str, object]) -> str:
    normalized = canonical_json_object(payload)
    encoded = json.dumps(
        normalized,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _assert_audit_payload_safe(value: JsonValue) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if key.casefold() in FORBIDDEN_AUDIT_KEYS:
                raise ValueError(f"audit payload contains forbidden key {key!r}")
            _assert_audit_payload_safe(child)
    elif isinstance(value, list):
        for child in value:
            _assert_audit_payload_safe(child)


class TenantScopedRepository:
    def __init__(self, session: AsyncSession, tenant_id: str) -> None:
        if not tenant_id:
            raise ValueError("tenant_id cannot be empty")
        self._session = session
        self._tenant_id = tenant_id

    def _assert_tenant(self, tenant_id: str) -> None:
        if tenant_id != self._tenant_id:
            raise TenantScopeViolation(self._tenant_id, tenant_id)


class AggregateRepository(TenantScopedRepository):
    async def get(self, aggregate_type: str, aggregate_id: str) -> AggregateRecord | None:
        model = await self._session.get(
            AggregateRecordModel,
            (self._tenant_id, aggregate_type, aggregate_id),
        )
        return None if model is None else self._to_record(model)

    async def add(self, record: AggregateRecord) -> AggregateRecord:
        self._assert_tenant(record.tenant_id)
        if record.version != 1:
            raise ValueError("a newly persisted aggregate must start at version 1")
        model = AggregateRecordModel(
            tenant_id=record.tenant_id,
            aggregate_type=record.aggregate_type,
            aggregate_id=record.aggregate_id,
            state=record.state,
            version=record.version,
            snapshot=canonical_json_object(record.snapshot),
            created_at=_as_utc(record.created_at),
            updated_at=_as_utc(record.updated_at),
        )
        self._session.add(model)
        await self._session.flush()
        return self._to_record(model)

    async def save(self, record: AggregateRecord, *, expected_version: int) -> AggregateRecord:
        self._assert_tenant(record.tenant_id)
        if expected_version < 1:
            raise ValueError("expected_version must be positive")
        statement = (
            update(AggregateRecordModel)
            .where(
                AggregateRecordModel.tenant_id == self._tenant_id,
                AggregateRecordModel.aggregate_type == record.aggregate_type,
                AggregateRecordModel.aggregate_id == record.aggregate_id,
                AggregateRecordModel.version == expected_version,
            )
            .values(
                state=record.state,
                snapshot=canonical_json_object(record.snapshot),
                version=expected_version + 1,
                updated_at=_as_utc(record.updated_at),
            )
        )
        result = await self._session.execute(statement)
        if result.rowcount != 1:  # type: ignore[attr-defined]
            raise OptimisticLockConflict(record.aggregate_id, expected_version)
        return replace(record, version=expected_version + 1, updated_at=_as_utc(record.updated_at))

    @staticmethod
    def _to_record(model: AggregateRecordModel) -> AggregateRecord:
        return AggregateRecord(
            tenant_id=model.tenant_id,
            aggregate_type=model.aggregate_type,
            aggregate_id=model.aggregate_id,
            state=model.state,
            version=model.version,
            snapshot=cast(JsonObject, dict(model.snapshot)),
            created_at=_as_utc(model.created_at),
            updated_at=_as_utc(model.updated_at),
        )


class AuditEventRepository(TenantScopedRepository):
    async def append(self, events: Sequence[AuditEventRecord | AuditEventDTO]) -> None:
        for event in events:
            payload = canonical_json_object(event.payload)
            _assert_audit_payload_safe(payload)
            actor_type = str(event.actor_type)
            aggregate_version = getattr(event, "aggregate_version", None)
            self._session.add(
                AuditEventModel(
                    event_id=event.event_id,
                    tenant_id=self._tenant_id,
                    event_type=event.event_type,
                    aggregate_type=event.aggregate_type,
                    aggregate_id=event.aggregate_id,
                    aggregate_version=aggregate_version,
                    actor_type=actor_type,
                    actor_id=event.actor_id,
                    occurred_at=_as_utc(event.occurred_at),
                    previous_state=event.previous_state,
                    new_state=event.new_state,
                    correlation_id=event.correlation_id,
                    causation_id=event.causation_id,
                    agent_run_id=event.agent_run_id,
                    idempotency_key=event.idempotency_key,
                    payload=payload,
                )
            )
        await self._session.flush()

    async def list_for_aggregate(
        self, aggregate_type: str, aggregate_id: str
    ) -> tuple[AuditEventRecord, ...]:
        statement: Select[tuple[AuditEventModel]] = (
            select(AuditEventModel)
            .where(
                AuditEventModel.tenant_id == self._tenant_id,
                AuditEventModel.aggregate_type == aggregate_type,
                AuditEventModel.aggregate_id == aggregate_id,
            )
            .order_by(AuditEventModel.aggregate_version, AuditEventModel.position)
        )
        models = (await self._session.scalars(statement)).all()
        return tuple(self._to_record(model) for model in models)

    @staticmethod
    def _to_record(model: AuditEventModel) -> AuditEventRecord:
        return AuditEventRecord(
            event_id=model.event_id,
            event_type=model.event_type,
            aggregate_type=model.aggregate_type,
            aggregate_id=model.aggregate_id,
            actor_type=model.actor_type,
            actor_id=model.actor_id,
            occurred_at=_as_utc(model.occurred_at),
            previous_state=model.previous_state,
            new_state=model.new_state,
            correlation_id=model.correlation_id,
            causation_id=model.causation_id,
            agent_run_id=model.agent_run_id,
            idempotency_key=model.idempotency_key,
            payload=cast(JsonObject, dict(model.payload)),
            aggregate_version=model.aggregate_version,
        )


class IdempotencyRepository(TenantScopedRepository):
    async def reserve(
        self,
        *,
        operation: str,
        idempotency_key: str,
        request_payload: Mapping[str, object],
        now: datetime,
    ) -> IdempotencyClaim:
        fingerprint = fingerprint_payload(request_payload)
        statement = (
            pg_insert(IdempotencyRecordModel)
            .values(
                tenant_id=self._tenant_id,
                operation=operation,
                idempotency_key=idempotency_key,
                request_fingerprint=fingerprint,
                status="PENDING",
                created_at=_as_utc(now),
            )
            .on_conflict_do_nothing(index_elements=["tenant_id", "operation", "idempotency_key"])
            .returning(IdempotencyRecordModel.idempotency_key)
        )
        inserted_key = (await self._session.execute(statement)).scalar_one_or_none()
        if inserted_key is not None:
            return IdempotencyClaim(claimed=True, request_fingerprint=fingerprint)

        existing = await self._required(operation, idempotency_key)
        if existing.request_fingerprint != fingerprint:
            raise IdempotencyConflict(idempotency_key)
        if existing.status != "COMPLETED":
            raise IdempotencyInProgress(idempotency_key)
        response = (
            None
            if existing.response_payload is None
            else cast(JsonObject, dict(existing.response_payload))
        )
        return IdempotencyClaim(
            claimed=False,
            request_fingerprint=fingerprint,
            outcome_kind=existing.outcome_kind,
            response_payload=response,
            error_code=existing.error_code,
        )

    async def complete(
        self,
        *,
        operation: str,
        idempotency_key: str,
        outcome_kind: Literal["SUCCESS", "DOMAIN_ERROR"],
        response_payload: Mapping[str, object],
        completed_at: datetime,
        error_code: str | None = None,
    ) -> IdempotencyClaim:
        existing = await self._required(operation, idempotency_key, for_update=True)
        normalized_response = canonical_json_object(response_payload)
        if existing.status == "COMPLETED":
            stored_response = (
                None
                if existing.response_payload is None
                else cast(JsonObject, dict(existing.response_payload))
            )
            return IdempotencyClaim(
                claimed=False,
                request_fingerprint=existing.request_fingerprint,
                outcome_kind=existing.outcome_kind,
                response_payload=stored_response,
                error_code=existing.error_code,
            )
        existing.status = "COMPLETED"
        existing.outcome_kind = outcome_kind
        existing.response_payload = normalized_response
        existing.error_code = error_code
        existing.completed_at = _as_utc(completed_at)
        await self._session.flush()
        return IdempotencyClaim(
            claimed=True,
            request_fingerprint=existing.request_fingerprint,
            outcome_kind=outcome_kind,
            response_payload=normalized_response,
            error_code=error_code,
        )

    async def _required(
        self, operation: str, idempotency_key: str, *, for_update: bool = False
    ) -> IdempotencyRecordModel:
        statement = select(IdempotencyRecordModel).where(
            IdempotencyRecordModel.tenant_id == self._tenant_id,
            IdempotencyRecordModel.operation == operation,
            IdempotencyRecordModel.idempotency_key == idempotency_key,
        )
        if for_update:
            statement = statement.with_for_update()
        existing = await self._session.scalar(statement)
        if existing is None:
            raise RecordNotFound("idempotency record", idempotency_key)
        return existing


class ConsumedLinkNonceRepository(TenantScopedRepository):
    async def consume(self, record: ConsumedLinkNonce) -> bool:
        self._assert_tenant(record.tenant_id)
        expires_at = _as_utc(record.expires_at)
        consumed_at = _as_utc(record.consumed_at)
        if consumed_at >= expires_at:
            raise ValueError("an expired nonce cannot be consumed")
        statement = (
            pg_insert(ConsumedLinkNonceModel)
            .values(
                tenant_id=record.tenant_id,
                purpose=record.purpose,
                nonce_hash=record.nonce_hash,
                subject_id=record.subject_id,
                expires_at=expires_at,
                consumed_at=consumed_at,
            )
            .on_conflict_do_nothing(index_elements=["tenant_id", "purpose", "nonce_hash"])
            .returning(ConsumedLinkNonceModel.nonce_hash)
        )
        return (await self._session.execute(statement)).scalar_one_or_none() is not None
