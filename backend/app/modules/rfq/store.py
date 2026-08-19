from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.modules.rfq.contracts import AuditEventDTO


@dataclass
class ExecutionStore:
    """Unit-of-work state buffer with no persistence policy of its own."""

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
    audit_events: list[AuditEventDTO] = field(default_factory=list)
    id_counters: dict[str, int] = field(default_factory=dict)


class InMemoryExecutionStore(ExecutionStore):
    """Explicit test/prototype adapter that is forbidden in the live graph."""


__all__ = ["ExecutionStore", "InMemoryExecutionStore"]
