"""Safe JSON codec for durable Dev 4 execution state.

The codec deliberately uses an allowlist of Pydantic DTOs already declared in
``app.modules.rfq.contracts``.  It never imports a class named by persisted
data and never executes persisted payloads (unlike pickle-style codecs).
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from datetime import date, datetime, time
from enum import Enum
from typing import Any

from pydantic import BaseModel

from app.modules.rfq import contracts
from app.modules.rfq.contracts import ContractDTO

_KIND = "__canal_agente_kind__"


class StateCodecError(ValueError):
    """Persisted state is unsupported, malformed, or refers to an unknown DTO."""


def _known_dtos() -> dict[str, type[ContractDTO]]:
    """Build an allowlist from the already-imported contract module."""

    result: dict[str, type[ContractDTO]] = {}
    for name, candidate in vars(contracts).items():
        if (
            isinstance(candidate, type)
            and issubclass(candidate, ContractDTO)
            and candidate is not ContractDTO
        ):
            result[name] = candidate
    return result


KNOWN_DTOS = _known_dtos()


def encode_state(value: Any) -> Any:
    """Convert supported Python/domain values to JSON-native tagged values."""

    if isinstance(value, BaseModel):
        model_name = type(value).__name__
        if model_name not in KNOWN_DTOS or not isinstance(value, ContractDTO):
            raise StateCodecError(f"Pydantic model is not an allowed contract DTO: {model_name}")
        return {
            _KIND: "dto",
            "name": model_name,
            "data": encode_state(value.model_dump(mode="python")),
        }
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise StateCodecError("naive datetime values cannot be persisted")
        return {_KIND: "datetime", "value": value.isoformat()}
    if isinstance(value, date):
        return {_KIND: "date", "value": value.isoformat()}
    if isinstance(value, time):
        return {_KIND: "time", "value": value.isoformat()}
    if isinstance(value, Enum):
        return encode_state(value.value)
    if isinstance(value, Mapping):
        return {
            _KIND: "mapping",
            "items": [[encode_state(key), encode_state(item)] for key, item in value.items()],
        }
    if isinstance(value, list):
        return {_KIND: "list", "items": [encode_state(item) for item in value]}
    if isinstance(value, tuple):
        return {_KIND: "tuple", "items": [encode_state(item) for item in value]}
    if isinstance(value, (set, frozenset)):
        items = [encode_state(item) for item in value]
        items.sort(key=canonical_encoded_json)
        return {_KIND: "set", "items": items}
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise StateCodecError("non-finite floats cannot be persisted")
        return value
    raise StateCodecError(f"unsupported persisted value type: {type(value).__name__}")


def decode_state(value: Any) -> Any:
    """Rehydrate a value produced by :func:`encode_state`."""

    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    if not isinstance(value, dict):
        raise StateCodecError("encoded state nodes must be scalars or tagged objects")
    kind = value.get(_KIND)
    if kind == "dto":
        _require_keys(value, {_KIND, "name", "data"})
        model_name = value["name"]
        model = KNOWN_DTOS.get(model_name)
        if model is None:
            raise StateCodecError(f"unknown contract DTO in persisted state: {model_name!r}")
        data = decode_state(value["data"])
        if not isinstance(data, dict):
            raise StateCodecError("DTO data must decode to a mapping")
        return model.model_validate(data)
    if kind == "datetime":
        _require_keys(value, {_KIND, "value"})
        parsed = datetime.fromisoformat(_require_string(value["value"], "datetime"))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise StateCodecError("persisted datetime does not contain a timezone")
        return parsed
    if kind == "date":
        _require_keys(value, {_KIND, "value"})
        return date.fromisoformat(_require_string(value["value"], "date"))
    if kind == "time":
        _require_keys(value, {_KIND, "value"})
        return time.fromisoformat(_require_string(value["value"], "time"))
    if kind in {"list", "tuple", "set"}:
        _require_keys(value, {_KIND, "items"})
        items = value["items"]
        if not isinstance(items, list):
            raise StateCodecError(f"{kind} items must be a list")
        decoded = [decode_state(item) for item in items]
        if kind == "tuple":
            return tuple(decoded)
        if kind == "set":
            return set(decoded)
        return decoded
    if kind == "mapping":
        _require_keys(value, {_KIND, "items"})
        items = value["items"]
        if not isinstance(items, list):
            raise StateCodecError("mapping items must be a list")
        result: dict[Any, Any] = {}
        for pair in items:
            if not isinstance(pair, list) or len(pair) != 2:
                raise StateCodecError("mapping entries must be key/value pairs")
            key = decode_state(pair[0])
            try:
                if key in result:
                    raise StateCodecError("persisted mapping contains duplicate keys")
                result[key] = decode_state(pair[1])
            except TypeError as error:
                raise StateCodecError("persisted mapping key is not hashable") from error
        return result
    raise StateCodecError(f"unknown or missing persisted state tag: {kind!r}")


def canonical_encoded_json(value: Any) -> str:
    """Canonical representation used for integrity checks and stable keys."""

    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _require_keys(value: dict[str, Any], expected: set[str]) -> None:
    if set(value) != expected:
        raise StateCodecError(
            f"malformed {value.get(_KIND)!r} node; expected fields {sorted(expected)}"
        )


def _require_string(value: Any, label: str) -> str:
    if not isinstance(value, str):
        raise StateCodecError(f"persisted {label} value must be a string")
    return value


__all__ = [
    "KNOWN_DTOS",
    "StateCodecError",
    "canonical_encoded_json",
    "decode_state",
    "encode_state",
]
