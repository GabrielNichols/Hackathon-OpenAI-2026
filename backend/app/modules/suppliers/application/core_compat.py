from __future__ import annotations

import base64
import hashlib
import hmac
import json
from collections.abc import Sequence
from datetime import datetime
from enum import StrEnum
from typing import Any, Literal, Protocol
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator


class Clock(Protocol):
    def now(self) -> datetime: ...


class IdGenerator(Protocol):
    def new(self, prefix: str) -> str: ...


class FixedClock:
    def __init__(self, value: datetime) -> None:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("fixed clock requires an aware datetime")
        self._value = value

    def now(self) -> datetime:
        return self._value


class UuidIdGenerator:
    def new(self, prefix: str) -> str:
        return f"{prefix}_{uuid4().hex}"


class SupplierLifecycleStatus(StrEnum):
    DRAFT = "DRAFT"
    MATERIALS_UPLOADED = "MATERIALS_UPLOADED"
    EXTRACTED = "EXTRACTED"
    AWAITING_SUPPLIER_REVIEW = "AWAITING_SUPPLIER_REVIEW"
    CONFIRMED = "CONFIRMED"
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"
    EXPIRED = "EXPIRED"


class ReviewTokenClaims(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    purpose: str
    tenant_id: str = Field(min_length=1)
    supplier_id: str = Field(min_length=1)
    recipient_id: str = Field(min_length=1)
    expires_at: datetime
    nonce: str = Field(min_length=1)

    @field_validator("expires_at")
    @classmethod
    def expiration_is_timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("expires_at must be timezone-aware")
        return value


class ReviewTokenError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class SignedReviewTokenService:
    PURPOSE = "supplier_profile_review"

    def __init__(self, *, secret: bytes, clock: Clock) -> None:
        if not secret:
            raise ValueError("review token secret cannot be empty")
        self._secret = secret
        self._clock = clock
        self._consumed_nonces: set[str] = set()

    def issue(
        self,
        *,
        tenant_id: str,
        supplier_id: str,
        recipient_id: str,
        expires_at: datetime,
        nonce: str,
        purpose: str = PURPOSE,
    ) -> str:
        claims = ReviewTokenClaims(
            purpose=purpose,
            tenant_id=tenant_id,
            supplier_id=supplier_id,
            recipient_id=recipient_id,
            expires_at=expires_at,
            nonce=nonce,
        )
        payload = json.dumps(
            claims.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        encoded_payload = self._encode(payload)
        signature = hmac.new(self._secret, encoded_payload.encode(), hashlib.sha256).digest()
        return f"{encoded_payload}.{self._encode(signature)}"

    def validate(
        self,
        token: str,
        *,
        expected_tenant_id: str | None = None,
        expected_supplier_id: str | None = None,
        expected_recipient_id: str | None = None,
        expected_purpose: str = PURPOSE,
    ) -> ReviewTokenClaims:
        try:
            encoded_payload, encoded_signature = token.split(".")
            supplied_signature = self._decode(encoded_signature)
            expected_signature = hmac.new(
                self._secret,
                encoded_payload.encode(),
                hashlib.sha256,
            ).digest()
            if not hmac.compare_digest(supplied_signature, expected_signature):
                raise ReviewTokenError("LINK_INVALID", "invalid review link")
            claims = ReviewTokenClaims.model_validate_json(self._decode(encoded_payload))
        except ReviewTokenError:
            raise
        except (ValueError, ValidationError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ReviewTokenError("LINK_INVALID", "invalid review link") from error

        if self._clock.now() >= claims.expires_at:
            raise ReviewTokenError("LINK_EXPIRED", "review link expired")
        bindings = (
            (claims.purpose, expected_purpose),
            (claims.tenant_id, expected_tenant_id),
            (claims.supplier_id, expected_supplier_id),
            (claims.recipient_id, expected_recipient_id),
        )
        if any(
            expected is not None and not hmac.compare_digest(actual, expected)
            for actual, expected in bindings
        ):
            raise ReviewTokenError("LINK_INVALID", "invalid review link binding")
        return claims

    def consume_for_submit(
        self,
        token: str,
        *,
        expected_tenant_id: str | None = None,
        expected_supplier_id: str | None = None,
        expected_recipient_id: str | None = None,
        expected_purpose: str = PURPOSE,
    ) -> ReviewTokenClaims:
        claims = self.validate(
            token,
            expected_tenant_id=expected_tenant_id,
            expected_supplier_id=expected_supplier_id,
            expected_recipient_id=expected_recipient_id,
            expected_purpose=expected_purpose,
        )
        if claims.nonce in self._consumed_nonces:
            raise ReviewTokenError("LINK_INVALID", "review link final action was already used")
        self._consumed_nonces.add(claims.nonce)
        return claims

    @staticmethod
    def _encode(value: bytes) -> str:
        return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")

    @staticmethod
    def _decode(value: str) -> bytes:
        padding = "=" * (-len(value) % 4)
        return base64.b64decode(value + padding, altchars=b"-_", validate=True)


class ActivateSupplierCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    tenant_id: str
    supplier_id: str
    review_submission_id: str
    required_fields: tuple[str, ...]
    confirmed_fields: tuple[str, ...]
    idempotency_key: str
    correlation_id: str


class SupplierActivationResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    supplier_id: str
    status: SupplierLifecycleStatus
    version: int = Field(ge=1)
    activated_at: datetime


class SupplierActivationCommandPort(Protocol):
    async def activate(self, command: ActivateSupplierCommand) -> SupplierActivationResult: ...


class FakeSupplierActivationCommandPort:
    """Replaceable fake for the not-yet-published core SupplierAggregate handler."""

    def __init__(self, *, clock: Clock, error: Exception | None = None) -> None:
        self._clock = clock
        self._error = error
        self.commands: list[ActivateSupplierCommand] = []
        self._version = 0

    async def activate(self, command: ActivateSupplierCommand) -> SupplierActivationResult:
        self.commands.append(command)
        if self._error is not None:
            raise self._error
        if set(command.required_fields) - set(command.confirmed_fields):
            raise ValueError("activation command does not prove every required field")
        self._version += 1
        return SupplierActivationResult(
            supplier_id=command.supplier_id,
            status=SupplierLifecycleStatus.ACTIVE,
            version=self._version,
            activated_at=self._clock.now(),
        )


class SupplierAuditEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    event_id: str
    event_type: str
    aggregate_type: Literal["supplier"] = "supplier"
    aggregate_id: str
    actor_type: Literal["supplier", "system"]
    actor_id: str | None
    occurred_at: datetime
    previous_state: str | None = None
    new_state: str | None = None
    correlation_id: str
    causation_id: str | None = None
    agent_run_id: str | None = None
    idempotency_key: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class AuditPort(Protocol):
    async def append(self, events: Sequence[SupplierAuditEvent]) -> None: ...


class FakeAuditPort:
    def __init__(self) -> None:
        self.events: list[SupplierAuditEvent] = []

    async def append(self, events: Sequence[SupplierAuditEvent]) -> None:
        self.events.extend(events)
