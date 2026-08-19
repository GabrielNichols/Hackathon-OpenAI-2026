"""Tenant-scoped transactional outbox with leasing and delivery deduplication."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import cast

from sqlalchemy import and_, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.contracts import OutboxStatus
from app.infrastructure.db.errors import (
    IdempotencyConflict,
    OutboxStateConflict,
    RecordNotFound,
    TenantScopeViolation,
)
from app.infrastructure.db.models import OutboxItemModel
from app.infrastructure.db.records import JsonObject
from app.infrastructure.db.repositories import canonical_json_object, fingerprint_payload

from .records import OutboxItem


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamps must be timezone-aware")
    return value.astimezone(UTC)


class OutboxRepository:
    def __init__(self, session: AsyncSession, tenant_id: str) -> None:
        if not tenant_id:
            raise ValueError("tenant_id cannot be empty")
        self._session = session
        self._tenant_id = tenant_id

    async def get(self, item_id: str) -> OutboxItem | None:
        model = await self._session.scalar(
            select(OutboxItemModel).where(
                OutboxItemModel.id == item_id,
                OutboxItemModel.tenant_id == self._tenant_id,
            )
        )
        return None if model is None else self._to_record(model)

    async def enqueue(self, item: OutboxItem) -> tuple[OutboxItem, bool]:
        self._assert_tenant(item.tenant_id)
        if item.status is not OutboxStatus.PENDING or item.attempt_count != 0:
            raise ValueError("new outbox items must be pending and unattempted")
        normalized_payload = canonical_json_object(item.payload)
        fingerprint = fingerprint_payload(
            {
                "aggregate_id": item.aggregate_id,
                "aggregate_type": item.aggregate_type,
                "kind": item.kind,
                "payload": normalized_payload,
            }
        )
        statement = (
            pg_insert(OutboxItemModel)
            .values(
                id=item.id,
                tenant_id=item.tenant_id,
                kind=item.kind,
                aggregate_type=item.aggregate_type,
                aggregate_id=item.aggregate_id,
                payload=normalized_payload,
                payload_fingerprint=fingerprint,
                idempotency_key=item.idempotency_key,
                status=OutboxStatus.PENDING.value,
                attempt_count=0,
                next_attempt_at=(
                    None if item.next_attempt_at is None else _as_utc(item.next_attempt_at)
                ),
                created_at=_as_utc(item.created_at),
                updated_at=_as_utc(item.updated_at),
            )
            .on_conflict_do_nothing(index_elements=["tenant_id", "idempotency_key"])
            .returning(OutboxItemModel.id)
        )
        inserted_id = (await self._session.execute(statement)).scalar_one_or_none()
        if inserted_id is not None:
            persisted = replace(
                item,
                payload=normalized_payload,
                created_at=_as_utc(item.created_at),
                updated_at=_as_utc(item.updated_at),
                next_attempt_at=(
                    None if item.next_attempt_at is None else _as_utc(item.next_attempt_at)
                ),
            )
            return persisted, True

        existing = await self._required_by_key(item.idempotency_key)
        if existing.payload_fingerprint != fingerprint:
            raise IdempotencyConflict(item.idempotency_key)
        return self._to_record(existing), False

    async def claim_due(
        self,
        *,
        worker_id: str,
        now: datetime,
        lease_for: timedelta,
        limit: int = 25,
    ) -> tuple[OutboxItem, ...]:
        if not worker_id:
            raise ValueError("worker_id cannot be empty")
        if lease_for <= timedelta(0):
            raise ValueError("lease_for must be positive")
        if limit < 1:
            raise ValueError("limit must be positive")
        current_time = _as_utc(now)
        due_pending = and_(
            OutboxItemModel.status.in_([OutboxStatus.PENDING.value, OutboxStatus.FAILED.value]),
            or_(
                OutboxItemModel.next_attempt_at.is_(None),
                OutboxItemModel.next_attempt_at <= current_time,
            ),
        )
        abandoned_processing = and_(
            OutboxItemModel.status == OutboxStatus.PROCESSING.value,
            OutboxItemModel.locked_until.is_not(None),
            OutboxItemModel.locked_until <= current_time,
        )
        statement = (
            select(OutboxItemModel)
            .where(
                OutboxItemModel.tenant_id == self._tenant_id,
                or_(due_pending, abandoned_processing),
            )
            .order_by(
                OutboxItemModel.next_attempt_at.asc().nullsfirst(),
                OutboxItemModel.created_at,
                OutboxItemModel.id,
            )
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        models = list((await self._session.scalars(statement)).all())
        for model in models:
            model.status = OutboxStatus.PROCESSING.value
            model.attempt_count += 1
            model.locked_by = worker_id
            model.locked_until = current_time + lease_for
            model.updated_at = current_time
            model.last_error = None
        await self._session.flush()
        return tuple(self._to_record(model) for model in models)

    async def mark_failed(
        self,
        item_id: str,
        *,
        worker_id: str,
        attempt_count: int,
        error: str,
        next_attempt_at: datetime,
        now: datetime,
    ) -> OutboxItem:
        model = await self._required(item_id, for_update=True)
        current_time = _as_utc(now)
        self._assert_active_lease(
            model, worker_id=worker_id, attempt_count=attempt_count, now=current_time
        )
        model.status = OutboxStatus.FAILED.value
        model.last_error = error.strip()[:2000]
        model.next_attempt_at = _as_utc(next_attempt_at)
        model.locked_by = None
        model.locked_until = None
        model.updated_at = current_time
        await self._session.flush()
        return self._to_record(model)

    async def mark_delivered(
        self,
        item_id: str,
        *,
        worker_id: str,
        attempt_count: int,
        external_delivery_id: str,
        delivered_at: datetime,
    ) -> OutboxItem:
        model = await self._required(item_id, for_update=True)
        if model.status == OutboxStatus.DELIVERED.value:
            if model.external_delivery_id != external_delivery_id:
                raise OutboxStateConflict(item_id, model.status, "DELIVERED_WITH_DIFFERENT_ACK")
            return self._to_record(model)
        timestamp = _as_utc(delivered_at)
        self._assert_active_lease(
            model, worker_id=worker_id, attempt_count=attempt_count, now=timestamp
        )
        model.status = OutboxStatus.DELIVERED.value
        model.external_delivery_id = external_delivery_id
        model.delivered_at = timestamp
        model.updated_at = timestamp
        model.next_attempt_at = None
        model.last_error = None
        model.locked_by = None
        model.locked_until = None
        await self._session.flush()
        return self._to_record(model)

    def _assert_active_lease(
        self,
        model: OutboxItemModel,
        *,
        worker_id: str,
        attempt_count: int,
        now: datetime,
    ) -> None:
        lease_is_current = (
            model.status == OutboxStatus.PROCESSING.value
            and model.locked_by == worker_id
            and model.attempt_count == attempt_count
            and model.locked_until is not None
            and _as_utc(model.locked_until) > now
        )
        if not lease_is_current:
            raise OutboxStateConflict(model.id, model.status, "STALE_OR_FOREIGN_LEASE")

    async def _required(self, item_id: str, *, for_update: bool = False) -> OutboxItemModel:
        statement = select(OutboxItemModel).where(
            OutboxItemModel.id == item_id,
            OutboxItemModel.tenant_id == self._tenant_id,
        )
        if for_update:
            statement = statement.with_for_update()
        model = await self._session.scalar(statement)
        if model is None:
            raise RecordNotFound("outbox item", item_id)
        return model

    async def _required_by_key(self, idempotency_key: str) -> OutboxItemModel:
        model = await self._session.scalar(
            select(OutboxItemModel).where(
                OutboxItemModel.tenant_id == self._tenant_id,
                OutboxItemModel.idempotency_key == idempotency_key,
            )
        )
        if model is None:
            raise RecordNotFound("outbox item", idempotency_key)
        return model

    def _assert_tenant(self, tenant_id: str) -> None:
        if tenant_id != self._tenant_id:
            raise TenantScopeViolation(self._tenant_id, tenant_id)

    @staticmethod
    def _to_record(model: OutboxItemModel) -> OutboxItem:
        return OutboxItem(
            id=model.id,
            tenant_id=model.tenant_id,
            kind=model.kind,
            aggregate_type=model.aggregate_type,
            aggregate_id=model.aggregate_id,
            payload=cast(JsonObject, dict(model.payload)),
            idempotency_key=model.idempotency_key,
            status=OutboxStatus(model.status),
            attempt_count=model.attempt_count,
            next_attempt_at=(
                None if model.next_attempt_at is None else _as_utc(model.next_attempt_at)
            ),
            last_error=model.last_error,
            external_delivery_id=model.external_delivery_id,
            locked_by=model.locked_by,
            locked_until=None if model.locked_until is None else _as_utc(model.locked_until),
            delivered_at=(None if model.delivered_at is None else _as_utc(model.delivered_at)),
            created_at=_as_utc(model.created_at),
            updated_at=_as_utc(model.updated_at),
        )
