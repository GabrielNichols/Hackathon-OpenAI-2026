"""Small deterministic runtime primitives shared by the Dev 4 domain modules."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, is_dataclass
from datetime import UTC, date, datetime, time
from decimal import Decimal
from enum import Enum
from typing import Any, Protocol
from uuid import uuid4


def ensure_utc(value: datetime) -> datetime:
    """Reject naive datetimes and normalize aware values to UTC."""

    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must include a timezone")
    return value.astimezone(UTC)


def utc_now() -> datetime:
    return datetime.now(UTC)


class Clock(Protocol):
    def now(self) -> datetime: ...


@dataclass(frozen=True, slots=True)
class SystemClock:
    def now(self) -> datetime:
        return utc_now()


@dataclass(frozen=True, slots=True)
class FixedClock:
    value: datetime

    def __post_init__(self) -> None:
        object.__setattr__(self, "value", ensure_utc(self.value))

    def now(self) -> datetime:
        return self.value


class IDGenerator(Protocol):
    def new(self, prefix: str) -> str: ...


@dataclass(frozen=True, slots=True)
class UUIDGenerator:
    def new(self, prefix: str) -> str:
        clean_prefix = prefix.strip().lower().replace(" ", "_")
        if not clean_prefix:
            raise ValueError("id prefix must not be empty")
        return f"{clean_prefix}_{uuid4().hex}"


def _json_value(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return _json_value(value.model_dump(mode="python"))
    if is_dataclass(value) and not isinstance(value, type):
        return _json_value(asdict(value))
    if isinstance(value, datetime):
        return ensure_utc(value).isoformat().replace("+00:00", "Z")
    if isinstance(value, (date, time)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Enum):
        return _json_value(value.value)
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if isinstance(value, (set, frozenset)):
        normalized = [_json_value(item) for item in value]
        return sorted(normalized, key=lambda item: json.dumps(item, sort_keys=True))
    return value


def canonical_json(value: Any) -> str:
    """Serialize a payload in a stable form suitable for idempotency hashes."""

    return json.dumps(
        _json_value(value),
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def payload_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()
