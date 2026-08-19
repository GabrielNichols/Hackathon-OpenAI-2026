"""Small validated values shared by aggregates."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TypeVar

from app.contracts import ErrorCode
from app.domain.common.errors import DomainError

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class Money:
    cents: int
    currency: str = "BRL"

    def __post_init__(self) -> None:
        if type(self.cents) is not int or self.cents < 0:
            raise DomainError(
                ErrorCode.VALIDATION_ERROR,
                "Money cents must be a non-negative integer",
                details={"field": "cents"},
            )
        if not self.currency.strip():
            raise DomainError(
                ErrorCode.VALIDATION_ERROR,
                "Money currency is required",
                details={"field": "currency"},
            )


def require_non_empty(value: str, *, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DomainError(
            ErrorCode.VALIDATION_ERROR,
            f"{field} is required",
            details={"field": field},
        )
    return value


def require_positive_int(value: int, *, field: str) -> int:
    if type(value) is not int or value <= 0:
        raise DomainError(
            ErrorCode.VALIDATION_ERROR,
            f"{field} must be a positive integer",
            details={"field": field},
        )
    return value


def require_prefixed_id(value: str, *, field: str, prefix: str) -> str:
    normalized = require_non_empty(value, field=field)
    if not normalized.startswith(prefix) or len(normalized) == len(prefix):
        raise DomainError(
            ErrorCode.VALIDATION_ERROR,
            f"{field} must start with {prefix}",
            details={"field": field, "expected_prefix": prefix},
        )
    return normalized


def require_utc(value: datetime, *, field: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise DomainError(
            ErrorCode.VALIDATION_ERROR,
            f"{field} must be timezone-aware",
            details={"field": field},
        )
    return value.astimezone(UTC)
