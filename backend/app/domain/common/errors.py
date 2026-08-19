"""Stable errors raised by pure domain behavior."""

from __future__ import annotations

from typing import Any

from app.contracts import ErrorCode


class DomainError(Exception):
    """A deterministic business failure suitable for an API error envelope."""

    def __init__(
        self,
        code: ErrorCode,
        message: str,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = dict(details or {})

    @classmethod
    def invalid_transition(
        cls,
        *,
        aggregate_type: str,
        previous_state: str,
        requested_state: str,
    ) -> DomainError:
        return cls(
            ErrorCode.INVALID_STATE_TRANSITION,
            f"{aggregate_type} cannot move from {previous_state} to {requested_state}",
            details={
                "aggregate_type": aggregate_type,
                "previous_state": previous_state,
                "requested_state": requested_state,
            },
        )
