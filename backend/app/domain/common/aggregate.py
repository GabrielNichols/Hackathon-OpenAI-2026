"""Minimal aggregate root mechanics used by the state machines."""

from __future__ import annotations

from collections.abc import Iterable
from enum import StrEnum
from typing import Any, ClassVar, TypeVar

from app.domain.common.errors import DomainError
from app.domain.common.events import DomainEvent
from app.domain.common.values import require_non_empty

StateT = TypeVar("StateT", bound=StrEnum)


class AggregateRoot[StateT: StrEnum]:
    """Snapshot aggregate with optimistic version and staged domain events."""

    aggregate_type: ClassVar[str]

    def __init__(
        self,
        *,
        aggregate_id: str,
        tenant_id: str,
        state: StateT,
        created_event_type: str,
    ) -> None:
        self._id = aggregate_id
        self.tenant_id = require_non_empty(tenant_id, field="tenant_id")
        self.state = state
        self.version = 1
        self._pending_events: list[DomainEvent] = [
            DomainEvent(
                event_type=created_event_type,
                aggregate_type=self.aggregate_type,
                aggregate_id=self._id,
                previous_state=None,
                new_state=state.value,
                aggregate_version=self.version,
            ),
        ]

    @property
    def id(self) -> str:
        return self._id

    @property
    def pending_events(self) -> tuple[DomainEvent, ...]:
        return tuple(self._pending_events)

    def pull_events(self) -> tuple[DomainEvent, ...]:
        events = self.pending_events
        self._pending_events.clear()
        return events

    def _require_state(self, allowed: Iterable[StateT], requested_state: StateT) -> None:
        if self.state not in frozenset(allowed):
            raise DomainError.invalid_transition(
                aggregate_type=self.aggregate_type,
                previous_state=self.state.value,
                requested_state=requested_state.value,
            )

    def _transition(
        self,
        *,
        allowed_from: Iterable[StateT],
        new_state: StateT,
        event_type: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        self._require_state(allowed_from, new_state)
        previous_state = self.state
        self.state = new_state
        self.version += 1
        self._pending_events.append(
            DomainEvent(
                event_type=event_type,
                aggregate_type=self.aggregate_type,
                aggregate_id=self.id,
                previous_state=previous_state.value,
                new_state=new_state.value,
                aggregate_version=self.version,
                payload=dict(payload or {}),
            ),
        )
