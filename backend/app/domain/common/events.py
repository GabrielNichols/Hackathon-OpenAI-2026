"""Domain events staged until an application unit of work commits them."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class DomainEvent:
    event_type: str
    aggregate_type: str
    aggregate_id: str
    previous_state: str | None
    new_state: str | None
    aggregate_version: int
    payload: dict[str, Any] = field(default_factory=dict)
