"""ORM-independent persistence records used by explicit domain mappers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

type JsonScalar = str | int | bool | None
type JsonValue = JsonScalar | list[JsonValue] | dict[str, JsonValue]
type JsonObject = dict[str, JsonValue]


@dataclass(frozen=True, slots=True)
class AggregateRecord:
    tenant_id: str
    aggregate_type: str
    aggregate_id: str
    state: str
    version: int
    snapshot: JsonObject
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class AuditEventRecord:
    event_id: str
    event_type: str
    aggregate_type: str
    aggregate_id: str
    actor_type: str
    actor_id: str | None
    occurred_at: datetime
    previous_state: str | None
    new_state: str | None
    correlation_id: str
    causation_id: str | None
    agent_run_id: str | None
    idempotency_key: str | None
    payload: JsonObject
    aggregate_version: int | None


@dataclass(frozen=True, slots=True)
class IdempotencyClaim:
    claimed: bool
    request_fingerprint: str
    outcome_kind: str | None = None
    response_payload: JsonObject | None = None
    error_code: str | None = None


@dataclass(frozen=True, slots=True)
class ConsumedLinkNonce:
    tenant_id: str
    purpose: str
    nonce_hash: str
    subject_id: str
    expires_at: datetime
    consumed_at: datetime
