"""Small HMAC-signed tokens for supplier response links.

This module intentionally avoids JWT dependencies.  Tokens carry only the
minimum routing claims, are authenticated with HMAC-SHA256, and must always be
validated against an expected purpose before they are trusted.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any


class TokenValidationError(ValueError):
    """Base class for token validation errors safe to map to domain errors."""


class MalformedTokenError(TokenValidationError):
    """The token cannot be decoded or lacks required claims."""


class InvalidTokenSignatureError(TokenValidationError):
    """The token payload was not signed by this service."""


class ExpiredTokenError(TokenValidationError):
    """The token reached or passed its expiration timestamp."""


class TokenClaimMismatchError(TokenValidationError):
    """Purpose or subject does not match the expected action context."""


@dataclass(frozen=True, slots=True)
class TokenClaims:
    purpose: str
    subject: str
    issued_at: datetime
    expires_at: datetime
    metadata: Mapping[str, Any]


class SignedTokenService:
    """Issue and validate compact, URL-safe HMAC tokens."""

    _VERSION = 1

    def __init__(
        self,
        secret: str | bytes,
        *,
        clock: Callable[[], datetime] | Any | None = None,
        default_ttl: timedelta = timedelta(hours=24),
    ) -> None:
        secret_bytes = secret.encode("utf-8") if isinstance(secret, str) else secret
        if not secret_bytes:
            raise ValueError("Token secret must not be empty")
        if default_ttl <= timedelta(0):
            raise ValueError("default_ttl must be positive")
        self._secret = secret_bytes
        self._clock = clock
        self._default_ttl = default_ttl

    def issue(
        self,
        purpose: str,
        subject: str,
        *,
        expires_at: datetime | None = None,
        ttl: timedelta | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> str:
        if not purpose or not subject:
            raise ValueError("Token purpose and subject must not be empty")
        if expires_at is not None and ttl is not None:
            raise ValueError("Use either expires_at or ttl, not both")

        issued_at = self._now()
        expiry = _as_utc(expires_at) if expires_at else issued_at + (ttl or self._default_ttl)
        if expiry <= issued_at:
            raise ValueError("Token expiration must be in the future")

        payload = {
            "v": self._VERSION,
            "purpose": purpose,
            "subject": subject,
            "iat": int(issued_at.timestamp()),
            "exp": int(expiry.timestamp()),
            "metadata": dict(metadata or {}),
        }
        encoded_payload = _b64encode(
            json.dumps(
                payload,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode("utf-8")
        )
        signature = hmac.new(
            self._secret,
            encoded_payload.encode("ascii"),
            hashlib.sha256,
        ).digest()
        return f"{encoded_payload}.{_b64encode(signature)}"

    def validate(
        self,
        token: str,
        *,
        purpose: str,
        subject: str | None = None,
        now: datetime | None = None,
    ) -> TokenClaims:
        if not purpose:
            raise ValueError("Expected token purpose must not be empty")
        try:
            encoded_payload, encoded_signature = token.split(".", maxsplit=1)
        except (AttributeError, ValueError) as error:
            raise MalformedTokenError("Token must contain payload and signature") from error

        expected_signature = hmac.new(
            self._secret,
            encoded_payload.encode("ascii"),
            hashlib.sha256,
        ).digest()
        try:
            supplied_signature = _b64decode(encoded_signature)
        except (ValueError, UnicodeEncodeError) as error:
            raise MalformedTokenError("Token signature is not valid base64url") from error
        if not hmac.compare_digest(supplied_signature, expected_signature):
            raise InvalidTokenSignatureError("Token signature is invalid")

        try:
            payload = json.loads(_b64decode(encoded_payload))
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise MalformedTokenError("Token payload is malformed") from error
        if not isinstance(payload, dict):
            raise MalformedTokenError("Token payload must be an object")

        try:
            version = payload["v"]
            actual_purpose = payload["purpose"]
            actual_subject = payload["subject"]
            issued_at = datetime.fromtimestamp(payload["iat"], UTC)
            expires_at = datetime.fromtimestamp(payload["exp"], UTC)
            metadata = payload.get("metadata", {})
        except (KeyError, TypeError, ValueError, OverflowError) as error:
            raise MalformedTokenError("Token has invalid or missing claims") from error

        if version != self._VERSION:
            raise MalformedTokenError("Unsupported token version")
        if not isinstance(actual_purpose, str) or not actual_purpose:
            raise MalformedTokenError("Token purpose is invalid")
        if not isinstance(actual_subject, str) or not actual_subject:
            raise MalformedTokenError("Token subject is invalid")
        if not isinstance(metadata, dict):
            raise MalformedTokenError("Token metadata must be an object")
        if actual_purpose != purpose:
            raise TokenClaimMismatchError("Token purpose does not match this action")
        if subject is not None and actual_subject != subject:
            raise TokenClaimMismatchError("Token subject does not match this resource")

        validation_time = _as_utc(now) if now is not None else self._now()
        if validation_time >= expires_at:
            raise ExpiredTokenError("Token has expired")

        return TokenClaims(
            purpose=actual_purpose,
            subject=actual_subject,
            issued_at=issued_at,
            expires_at=expires_at,
            metadata=metadata,
        )

    # Naming aliases keep call sites readable in different bounded contexts.
    mint = issue
    verify = validate

    def _now(self) -> datetime:
        if self._clock is None:
            value = datetime.now(UTC)
        elif callable(self._clock):
            value = self._clock()
        elif hasattr(self._clock, "now"):
            value = self._clock.now()
        else:
            raise TypeError("clock must be callable or expose now()")
        return _as_utc(value)


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _b64decode(value: str) -> bytes:
    if not value or any(character.isspace() for character in value):
        raise ValueError("Invalid base64url value")
    padding = "=" * (-len(value) % 4)
    try:
        return base64.b64decode(
            (value + padding).encode("ascii"),
            altchars=b"-_",
            validate=True,
        )
    except (ValueError, UnicodeEncodeError) as error:
        raise ValueError("Invalid base64url value") from error


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("Token timestamps must be timezone-aware")
    return value.astimezone(UTC)
