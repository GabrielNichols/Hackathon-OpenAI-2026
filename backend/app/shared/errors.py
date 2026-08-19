"""Typed domain errors used across the execution boundary."""

from __future__ import annotations

from enum import StrEnum
from typing import Any


class ErrorCode(StrEnum):
    VALIDATION_ERROR = "VALIDATION_ERROR"
    NOT_FOUND = "NOT_FOUND"
    INVALID_STATE = "INVALID_STATE"
    STALE_VERSION = "STALE_VERSION"
    IDEMPOTENCY_CONFLICT = "IDEMPOTENCY_CONFLICT"
    POLICY_DENIED = "POLICY_DENIED"
    DEPENDENCY_UNAVAILABLE = "DEPENDENCY_UNAVAILABLE"
    QUOTE_TOTAL_MISMATCH = "QUOTE_TOTAL_MISMATCH"
    QUOTE_EXPIRED = "QUOTE_EXPIRED"
    INVALID_RESPONSE_TOKEN = "INVALID_RESPONSE_TOKEN"


class DomainError(Exception):
    """Expected business failure; adapters may map ``code`` to their transport."""

    def __init__(
        self,
        code: ErrorCode | str,
        message: str | None = None,
        *,
        retryable: bool = False,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.code = code.value if isinstance(code, ErrorCode) else str(code)
        self.message = message or self.code
        self.retryable = retryable
        self.details = details or {}
        super().__init__(f"{self.code}: {self.message}")


def require(
    condition: bool,
    code: ErrorCode | str,
    message: str | None = None,
    *,
    details: dict[str, Any] | None = None,
) -> None:
    if not condition:
        raise DomainError(code, message, details=details)
