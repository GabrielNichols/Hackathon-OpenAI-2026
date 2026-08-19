"""ORM-independent outbox representation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from app.contracts import OutboxStatus
from app.infrastructure.db.records import JsonObject


@dataclass(frozen=True, slots=True)
class OutboxItem:
    id: str
    tenant_id: str
    kind: str
    aggregate_id: str
    payload: JsonObject
    idempotency_key: str
    created_at: datetime
    updated_at: datetime
    aggregate_type: str | None = None
    status: OutboxStatus = OutboxStatus.PENDING
    attempt_count: int = 0
    next_attempt_at: datetime | None = None
    last_error: str | None = None
    external_delivery_id: str | None = None
    locked_by: str | None = None
    locked_until: datetime | None = None
    delivered_at: datetime | None = None
