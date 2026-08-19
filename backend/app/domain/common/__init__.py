"""Shared domain primitives with no infrastructure dependencies."""

from app.domain.common.aggregate import AggregateRoot
from app.domain.common.errors import DomainError
from app.domain.common.events import DomainEvent
from app.domain.common.values import (
    Money,
    require_non_empty,
    require_positive_int,
    require_prefixed_id,
    require_utc,
)

__all__ = [
    "AggregateRoot",
    "DomainError",
    "DomainEvent",
    "Money",
    "require_non_empty",
    "require_positive_int",
    "require_prefixed_id",
    "require_utc",
]
