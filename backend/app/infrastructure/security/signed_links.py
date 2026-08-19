import base64
import hashlib
import hmac
import inspect
import json
from collections.abc import Awaitable
from datetime import UTC, datetime
from threading import Lock
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

from app.contracts import ErrorCode


class SignedLinkError(ValueError):
    def __init__(self, code: ErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.details: dict[str, Any] = {}


class SignedLinkPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    purpose: str
    subject_id: str
    recipient_id: str
    expires_at: datetime
    nonce: str
    tenant_id: str

    @field_validator("expires_at")
    @classmethod
    def normalize_expiry(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("expires_at must be timezone-aware")
        return value.astimezone(UTC)

    @field_validator("purpose", "subject_id", "recipient_id", "nonce", "tenant_id")
    @classmethod
    def reject_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("signed link fields cannot be blank")
        return value


class NonceRegistry(Protocol):
    """Atomic check-and-set boundary used to reject nonce replay."""

    def consume(
        self, payload: SignedLinkPayload, *, consumed_at: datetime
    ) -> bool | Awaitable[bool]: ...


def _encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _decode(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


class SignedLinkService:
    def __init__(self, secret: bytes) -> None:
        if len(secret) < 32:
            raise ValueError("Signed link secret must contain at least 32 bytes")
        self._secret = secret

    def issue(self, payload: SignedLinkPayload) -> str:
        body = json.dumps(
            payload.model_dump(mode="json"),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        encoded = _encode(body)
        signature = hmac.new(self._secret, encoded.encode("ascii"), hashlib.sha256).digest()
        return f"{encoded}.{_encode(signature)}"

    def verify(
        self,
        token: str,
        *,
        expected_purpose: str,
        expected_tenant_id: str,
        now: datetime,
        expected_subject_id: str | None = None,
    ) -> SignedLinkPayload:
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("now must be timezone-aware")
        try:
            encoded, supplied_signature = token.split(".", maxsplit=1)
            expected_signature = hmac.new(
                self._secret, encoded.encode("ascii"), hashlib.sha256
            ).digest()
            if not hmac.compare_digest(_decode(supplied_signature), expected_signature):
                raise SignedLinkError(ErrorCode.LINK_INVALID, "Signed link signature is invalid")
            payload = SignedLinkPayload.model_validate_json(_decode(encoded))
        except SignedLinkError:
            raise
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError, ValidationError) as error:
            raise SignedLinkError(ErrorCode.LINK_INVALID, "Signed link is invalid") from error

        if payload.expires_at <= now.astimezone(UTC):
            raise SignedLinkError(ErrorCode.LINK_EXPIRED, "Signed link has expired")
        if payload.purpose != expected_purpose:
            raise SignedLinkError(ErrorCode.LINK_INVALID, "Signed link purpose does not match")
        if payload.tenant_id != expected_tenant_id:
            raise SignedLinkError(ErrorCode.LINK_INVALID, "Signed link tenant does not match")
        if expected_subject_id is not None and payload.subject_id != expected_subject_id:
            raise SignedLinkError(ErrorCode.LINK_INVALID, "Signed link subject does not match")
        return payload

    async def verify_and_consume(
        self,
        token: str,
        *,
        registry: NonceRegistry,
        expected_purpose: str,
        expected_tenant_id: str,
        now: datetime,
        expected_subject_id: str | None = None,
    ) -> SignedLinkPayload:
        """Verify a capability and consume its nonce before returning it.

        Production registries must implement ``consume`` as an atomic, durable
        compare-and-set. Returning ``False`` means that the nonce was already used.
        """

        payload = self.verify(
            token,
            expected_purpose=expected_purpose,
            expected_tenant_id=expected_tenant_id,
            now=now,
            expected_subject_id=expected_subject_id,
        )
        consumed = registry.consume(payload, consumed_at=now.astimezone(UTC))
        if inspect.isawaitable(consumed):
            consumed = await consumed
        if consumed is not True:
            raise SignedLinkError(ErrorCode.LINK_INVALID, "Signed link nonce was already consumed")
        return payload


class TestOnlyInMemoryNonceRegistry:
    """Process-local registry for unit tests; never use it in production."""

    def __init__(self) -> None:
        self._consumed: set[tuple[str, str, str]] = set()
        self._lock = Lock()

    def consume(self, payload: SignedLinkPayload, *, consumed_at: datetime) -> bool:
        del consumed_at
        nonce_hash = hashlib.sha256(payload.nonce.encode("utf-8")).hexdigest()
        key = (payload.tenant_id, payload.purpose, nonce_hash)
        with self._lock:
            if key in self._consumed:
                return False
            self._consumed.add(key)
            return True


# Backwards-compatible import name. The explicit class name documents the scope.
InMemoryNonceRegistry = TestOnlyInMemoryNonceRegistry
