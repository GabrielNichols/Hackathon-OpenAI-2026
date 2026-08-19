from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class InMemoryExecutionStore:
    """Prototype persistence adapter.

    Application services own all state transitions; the store deliberately has
    no business logic so it can later be replaced by repositories from Dev 1.
    """

    rounds: dict[str, dict[str, Any]] = field(default_factory=dict)
    recipients: dict[str, dict[str, Any]] = field(default_factory=dict)
    quotes: dict[str, dict[str, Any]] = field(default_factory=dict)
    quote_versions: dict[tuple[str, int], Any] = field(default_factory=dict)
    comparisons: dict[str, dict[str, Any]] = field(default_factory=dict)
    approvals: dict[str, dict[str, Any]] = field(default_factory=dict)
    awards: dict[str, dict[str, Any]] = field(default_factory=dict)
    award_by_approval_id: dict[str, str] = field(default_factory=dict)
    reservations: dict[str, dict[str, Any]] = field(default_factory=dict)
    reservation_by_award_id: dict[str, str] = field(default_factory=dict)
    procurement_status: dict[str, str] = field(default_factory=dict)
    idempotency: dict[tuple[str, str], tuple[str, Any]] = field(default_factory=dict)
    audit_events: list[Any] = field(default_factory=list)
