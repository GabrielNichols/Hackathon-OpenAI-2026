import asyncio
import copy
import hashlib
import inspect
import json
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time
from enum import Enum
from typing import Any, TypeVar, cast

from pydantic import BaseModel

from app.contracts import ErrorCode

T = TypeVar("T")


class IdempotencyConflictError(RuntimeError):
    code = ErrorCode.IDEMPOTENCY_CONFLICT

    def __init__(self, key: str) -> None:
        super().__init__("Idempotency key was already used with a different payload")
        self.details = {"idempotency_key": key}


def _json_default(value: object) -> object:
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    raise TypeError(f"Unsupported payload type: {type(value).__name__}")


def _reject_floats(value: Any) -> None:
    if isinstance(value, float):
        raise ValueError("Floats are not allowed in idempotent command payloads")
    if isinstance(value, Mapping):
        for item in value.values():
            _reject_floats(item)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for item in value:
            _reject_floats(item)


def canonical_payload_hash(payload: Any) -> str:
    if isinstance(payload, BaseModel):
        payload = payload.model_dump(mode="json")
    _reject_floats(payload)
    encoded = json.dumps(
        payload,
        default=_json_default,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@dataclass(slots=True)
class _Record:
    fingerprint: str
    result: Any


class InMemoryIdempotencyRegistry:
    def __init__(self) -> None:
        self._records: dict[tuple[str, str, str], _Record] = {}
        self._lock = asyncio.Lock()

    async def execute(
        self,
        *,
        tenant_id: str,
        operation: str,
        key: str,
        payload: Any,
        handler: Callable[[], T | Awaitable[T]],
    ) -> T:
        record_key = (tenant_id, operation, key)
        fingerprint = canonical_payload_hash(payload)
        async with self._lock:
            existing = self._records.get(record_key)
            if existing is not None:
                if existing.fingerprint != fingerprint:
                    raise IdempotencyConflictError(key)
                return cast(T, copy.deepcopy(existing.result))
            result = handler()
            if inspect.isawaitable(result):
                result = await result
            self._records[record_key] = _Record(fingerprint, copy.deepcopy(result))
            return copy.deepcopy(result)
