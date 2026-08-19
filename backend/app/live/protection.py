"""Authenticated encryption for persisted execution-state JSON blobs."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
from typing import Any, Protocol

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_MARKER = "__canal_agente_encrypted_v1__"
_AAD = b"canal-agente/dev4-execution-state/v1"


class StateProtectionError(ValueError):
    """Encrypted state is malformed, was tampered with, or uses another key."""


class StateProtector(Protocol):
    def protect(self, value: Any) -> dict[str, str]: ...

    def unprotect(self, value: Any) -> Any: ...


class AesGcmStateProtector:
    """Encrypt JSON using AES-256-GCM and a domain-separated application key."""

    def __init__(self, secret: str | bytes) -> None:
        secret_bytes = secret.encode("utf-8") if isinstance(secret, str) else secret
        if len(secret_bytes) < 32:
            raise ValueError("state protection secret must contain at least 32 bytes")
        key = hmac.new(
            secret_bytes,
            b"canal-agente/state-encryption-key/v1",
            hashlib.sha256,
        ).digest()
        self._cipher = AESGCM(key)

    def protect(self, value: Any) -> dict[str, str]:
        plaintext = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        nonce = os.urandom(12)
        ciphertext = self._cipher.encrypt(nonce, plaintext, _AAD)
        return {
            _MARKER: _b64(nonce + ciphertext),
        }

    def unprotect(self, value: Any) -> Any:
        if not isinstance(value, dict) or set(value) != {_MARKER}:
            raise StateProtectionError("persisted state is not an encrypted v1 envelope")
        encoded = value.get(_MARKER)
        if not isinstance(encoded, str):
            raise StateProtectionError("encrypted state envelope is malformed")
        try:
            payload = _unb64(encoded)
            if len(payload) < 13:
                raise StateProtectionError("encrypted state envelope is too short")
            plaintext = self._cipher.decrypt(payload[:12], payload[12:], _AAD)
            return json.loads(plaintext)
        except (InvalidTag, UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
            if isinstance(error, StateProtectionError):
                raise
            raise StateProtectionError("persisted state authentication failed") from error


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii")


def _unb64(value: str) -> bytes:
    return base64.b64decode(value.encode("ascii"), altchars=b"-_", validate=True)


__all__ = ["AesGcmStateProtector", "StateProtectionError", "StateProtector"]
