"""Security helpers for live, capability-link based HTML forms."""

from __future__ import annotations

import hashlib
from datetime import timedelta

from app.shared.tokens import SignedTokenService, TokenValidationError


class CsrfValidationError(ValueError):
    """The form proof was absent, expired or bound to another action."""


class CsrfProtector:
    """Short-lived HMAC form proofs bound to an exact action context."""

    _PURPOSE = "live-form-csrf.v1"

    def __init__(
        self,
        secret: str | bytes,
        *,
        clock: object | None = None,
        ttl: timedelta = timedelta(minutes=30),
    ) -> None:
        secret_bytes = secret.encode("utf-8") if isinstance(secret, str) else secret
        if len(secret_bytes) < 32:
            raise ValueError("CSRF secret must contain at least 32 bytes")
        self._tokens = SignedTokenService(secret, clock=clock, default_ttl=ttl)

    def issue(self, action_context: str) -> str:
        return self._tokens.issue(self._PURPOSE, _context_digest(action_context))

    def verify(self, proof: str, action_context: str) -> None:
        try:
            self._tokens.validate(
                proof,
                purpose=self._PURPOSE,
                subject=_context_digest(action_context),
            )
        except TokenValidationError as error:
            raise CsrfValidationError("invalid or expired form proof") from error


def capability_fingerprint(token: str) -> str:
    """Create a safe binding value without copying a bearer token into claims."""

    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _context_digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


__all__ = ["CsrfProtector", "CsrfValidationError", "capability_fingerprint"]
