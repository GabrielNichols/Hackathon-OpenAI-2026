"""Replaceable boundaries used by procurement request interpretation."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from .schemas import (
    ProcurementInterpretationResult,
    ProcurementPolicySnapshot,
    RequestLike,
)


class Clock(Protocol):
    def now(self) -> datetime: ...


class InterpretationProviderError(RuntimeError):
    """Sanitized failure raised by an external interpretation provider."""

    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


class ProcurementInterpretationPort(Protocol):
    async def interpret(
        self,
        message: str,
        current_request: RequestLike | None = None,
        policy: ProcurementPolicySnapshot | None = None,
    ) -> ProcurementInterpretationResult: ...


__all__ = ["Clock", "InterpretationProviderError", "ProcurementInterpretationPort"]
