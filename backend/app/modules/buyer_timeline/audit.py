from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from datetime import datetime
from enum import StrEnum
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field


class ActorType(StrEnum):
    HUMAN = "human"
    SUPPLIER = "supplier"
    AGENT = "agent"
    SYSTEM = "system"
    EXTERNAL_SERVICE = "external_service"


class AuditEvent(BaseModel):
    """Small, serializable event envelope compatible with the Dev 1 contract."""

    model_config = ConfigDict(extra="forbid")

    event_id: str
    event_type: str
    aggregate_type: str
    aggregate_id: str
    actor_type: ActorType
    actor_id: str | None = None
    occurred_at: datetime
    previous_state: str | None = None
    new_state: str | None = None
    correlation_id: str
    causation_id: str | None = None
    agent_run_id: str | None = None
    idempotency_key: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class AuditPort(Protocol):
    async def append(self, events: Sequence[AuditEvent]) -> None: ...

    async def list_for_aggregate(self, aggregate_id: str) -> list[AuditEvent]: ...


class InMemoryAuditLog:
    """Prototype event log. It intentionally exposes a read model only to the buyer timeline."""

    def __init__(self) -> None:
        self._events: list[AuditEvent] = []
        self._by_aggregate: dict[str, list[AuditEvent]] = defaultdict(list)

    async def append(self, events: Sequence[AuditEvent]) -> None:
        for event in events:
            self._events.append(event)
            self._by_aggregate[event.aggregate_id].append(event)

    async def list_for_aggregate(self, aggregate_id: str) -> list[AuditEvent]:
        return list(self._by_aggregate.get(aggregate_id, ()))

    @property
    def events(self) -> tuple[AuditEvent, ...]:
        return tuple(self._events)
