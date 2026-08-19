"""Application configuration with secret-safe OpenAI loading."""

from __future__ import annotations

import math
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, cast

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OPENAI_KEY_FILE = PROJECT_ROOT / ".secrets" / "openai_api_key.txt"

InterpreterProvider = Literal["local", "auto", "openai"]


class ApplicationConfigurationError(RuntimeError):
    """A safe, user-facing configuration error without credential values."""


def load_interpreter_provider(value: str | None = None) -> InterpreterProvider:
    raw = value
    if raw is None:
        raw = os.getenv("PROCUREMENT_INTERPRETER", "local")
    normalized = raw.strip().casefold()
    if normalized not in {"local", "auto", "openai"}:
        raise ApplicationConfigurationError("INVALID_PROCUREMENT_INTERPRETER")
    return cast(InterpreterProvider, normalized)


def load_openai_api_key(
    secret_file: Path = DEFAULT_OPENAI_KEY_FILE,
) -> str | None:
    """Resolve the key from environment first and the ignored file second."""

    for env_name in ("OPENAI_API_KEY", "CANAL_AGENTE_OPENAI_API_KEY"):
        value = os.getenv(env_name, "").strip()
        if value:
            return value

    if not secret_file.is_file():
        return None

    value = secret_file.read_text(encoding="utf-8").strip()
    if not value:
        return None
    if "\n" in value or "\r" in value:
        raise ApplicationConfigurationError("INVALID_OPENAI_SECRET_FILE")
    return value


def _positive_float(
    env_name: str,
    default: float,
    *,
    maximum: float | None = None,
) -> float:
    raw = os.getenv(env_name)
    if raw is None:
        return default
    try:
        parsed = float(raw)
    except ValueError:
        raise ApplicationConfigurationError(f"INVALID_{env_name}") from None
    if not math.isfinite(parsed) or parsed <= 0 or (maximum is not None and parsed > maximum):
        raise ApplicationConfigurationError(f"INVALID_{env_name}")
    return parsed


def _bounded_int(env_name: str, default: int, *, minimum: int, maximum: int) -> int:
    raw = os.getenv(env_name)
    if raw is None:
        return default
    try:
        parsed = int(raw)
    except ValueError:
        raise ApplicationConfigurationError(f"INVALID_{env_name}") from None
    if not minimum <= parsed <= maximum:
        raise ApplicationConfigurationError(f"INVALID_{env_name}")
    return parsed


@dataclass(frozen=True, slots=True)
class OpenAIInterpreterSettings:
    api_key: str = field(repr=False)
    model: str = "gpt-5.6-luna"
    timeout_seconds: float = 15.0
    max_retries: int = 1
    max_output_tokens: int = 1_200

    @classmethod
    def from_environment(
        cls,
        *,
        secret_file: Path = DEFAULT_OPENAI_KEY_FILE,
    ) -> OpenAIInterpreterSettings:
        api_key = load_openai_api_key(secret_file)
        if api_key is None:
            raise ApplicationConfigurationError("OPENAI_CONFIGURATION_MISSING")
        model = os.getenv("OPENAI_PROCUREMENT_MODEL", "gpt-5.6-luna").strip()
        if not model:
            raise ApplicationConfigurationError("INVALID_OPENAI_PROCUREMENT_MODEL")
        return cls(
            api_key=api_key,
            model=model,
            timeout_seconds=_positive_float(
                "OPENAI_TIMEOUT_SECONDS",
                15.0,
                maximum=60.0,
            ),
            max_retries=_bounded_int("OPENAI_MAX_RETRIES", 1, minimum=0, maximum=5),
            max_output_tokens=_bounded_int(
                "OPENAI_MAX_OUTPUT_TOKENS",
                1_200,
                minimum=256,
                maximum=2_000,
            ),
        )


__all__ = [
    "DEFAULT_OPENAI_KEY_FILE",
    "PROJECT_ROOT",
    "ApplicationConfigurationError",
    "InterpreterProvider",
    "OpenAIInterpreterSettings",
    "load_interpreter_provider",
    "load_openai_api_key",
]
