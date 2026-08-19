"""Delivery gateway primitives and a deterministic in-memory fake.

The fake deliberately separates a gateway accepting a message from the
recipient delivery acknowledgment.  Tests can therefore prove that domain
state is not advanced optimistically.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Protocol


class DeliveryState(StrEnum):
    """States observable at the delivery-provider boundary."""

    SENT_TO_GATEWAY = "SENT_TO_GATEWAY"
    DELIVERED = "DELIVERED"
    FAILED = "FAILED"


class GatewayError(RuntimeError):
    """Base class for delivery-gateway failures."""


class GatewayIdempotencyConflict(GatewayError):
    """An idempotency key was reused for a different outbound message."""


class GatewayMessageNotFound(GatewayError):
    """No delivery record exists for the requested external identifier."""


@dataclass(frozen=True, slots=True)
class OutboundMessage:
    """Provider-independent message passed to a delivery gateway."""

    idempotency_key: str
    recipient_id: str
    supplier_id: str
    channel: str
    message_type: str
    body: str
    response_token: str
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        required = {
            "idempotency_key": self.idempotency_key,
            "recipient_id": self.recipient_id,
            "supplier_id": self.supplier_id,
            "channel": self.channel,
            "message_type": self.message_type,
            "response_token": self.response_token,
        }
        missing = [name for name, value in required.items() if not value]
        if missing:
            raise ValueError(f"OutboundMessage requires non-empty fields: {', '.join(missing)}")


@dataclass(slots=True)
class DeliveryRecord:
    """A single provider-side message record exposed by the fake."""

    external_id: str
    idempotency_key: str
    recipient_id: str
    supplier_id: str
    channel: str
    message_type: str
    body: str
    response_token: str
    metadata: dict[str, Any]
    fingerprint: str
    status: DeliveryState
    accepted_at: datetime
    delivered_at: datetime | None = None
    failed_at: datetime | None = None
    failure_reason: str | None = None


@dataclass(frozen=True, slots=True)
class GatewaySendResult:
    external_id: str
    status: DeliveryState
    accepted_at: datetime
    response_token: str
    idempotent_replay: bool = False


@dataclass(frozen=True, slots=True)
class GatewayDeliveryStatus:
    external_id: str
    status: DeliveryState
    delivered_at: datetime | None = None
    failed_at: datetime | None = None
    failure_reason: str | None = None


class DeliveryGateway(Protocol):
    async def send(self, message: OutboundMessage) -> GatewaySendResult: ...

    async def get_status(self, external_id: str) -> GatewayDeliveryStatus: ...


class FakeDeliveryGateway:
    """In-memory gateway with controlled acknowledgment and deduplication.

    ``auto_ack=False`` models a provider that accepted the request but has not
    yet confirmed delivery. ``ack(external_id)`` advances only that message.
    With ``auto_ack=True``, the message is acknowledged in the same ``send``
    call, which is convenient for the canonical happy-path demo.
    """

    def __init__(
        self,
        *,
        auto_ack: bool = False,
        clock: Callable[[], datetime] | Any | None = None,
    ) -> None:
        self.auto_ack = auto_ack
        self._clock = clock
        self.messages: list[DeliveryRecord] = []
        self._by_external_id: dict[str, DeliveryRecord] = {}
        self._by_idempotency_key: dict[str, DeliveryRecord] = {}

    async def send(self, message: OutboundMessage | Mapping[str, Any] | Any) -> GatewaySendResult:
        normalized = _normalize_message(message)
        fingerprint = _fingerprint(normalized)
        idempotency_key = normalized["idempotency_key"]

        existing = self._by_idempotency_key.get(idempotency_key)
        if existing is not None:
            if existing.fingerprint != fingerprint:
                raise GatewayIdempotencyConflict(
                    "The idempotency key is already bound to another message"
                )
            return _send_result(existing, idempotent_replay=True)

        accepted_at = self._now()
        external_id = f"fake_delivery_{len(self.messages) + 1:04d}"
        record = DeliveryRecord(
            external_id=external_id,
            idempotency_key=idempotency_key,
            recipient_id=normalized["recipient_id"],
            supplier_id=normalized["supplier_id"],
            channel=normalized["channel"],
            message_type=normalized["message_type"],
            body=normalized["body"],
            response_token=normalized["response_token"],
            metadata=dict(normalized["metadata"]),
            fingerprint=fingerprint,
            status=DeliveryState.SENT_TO_GATEWAY,
            accepted_at=accepted_at,
        )
        self.messages.append(record)
        self._by_external_id[external_id] = record
        self._by_idempotency_key[idempotency_key] = record

        if self.auto_ack:
            self.ack(external_id)
        return _send_result(record)

    async def get_status(self, external_id: str) -> GatewayDeliveryStatus:
        record = self._record(external_id)
        return GatewayDeliveryStatus(
            external_id=record.external_id,
            status=record.status,
            delivered_at=record.delivered_at,
            failed_at=record.failed_at,
            failure_reason=record.failure_reason,
        )

    def ack(
        self,
        external_id: str,
        *,
        delivered_at: datetime | None = None,
    ) -> GatewayDeliveryStatus:
        """Confirm one delivery. Repeated acknowledgments are idempotent."""

        record = self._record(external_id)
        if record.status == DeliveryState.FAILED:
            raise GatewayError("A failed delivery cannot be acknowledged")
        if record.status != DeliveryState.DELIVERED:
            record.status = DeliveryState.DELIVERED
            record.delivered_at = _as_utc(delivered_at or self._now())
        return GatewayDeliveryStatus(
            external_id=record.external_id,
            status=record.status,
            delivered_at=record.delivered_at,
        )

    def fail(
        self,
        external_id: str,
        *,
        reason: str = "fake provider failure",
        failed_at: datetime | None = None,
    ) -> GatewayDeliveryStatus:
        """Mark one accepted message as failed for failure-path tests."""

        record = self._record(external_id)
        if record.status == DeliveryState.DELIVERED:
            raise GatewayError("A delivered message cannot be marked as failed")
        if record.status != DeliveryState.FAILED:
            record.status = DeliveryState.FAILED
            record.failed_at = _as_utc(failed_at or self._now())
            record.failure_reason = reason
        return GatewayDeliveryStatus(
            external_id=record.external_id,
            status=record.status,
            failed_at=record.failed_at,
            failure_reason=record.failure_reason,
        )

    def _record(self, external_id: str) -> DeliveryRecord:
        try:
            return self._by_external_id[external_id]
        except KeyError as error:
            raise GatewayMessageNotFound(f"Unknown gateway external_id: {external_id}") from error

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


def _send_result(
    record: DeliveryRecord,
    *,
    idempotent_replay: bool = False,
) -> GatewaySendResult:
    return GatewaySendResult(
        external_id=record.external_id,
        status=record.status,
        accepted_at=record.accepted_at,
        response_token=record.response_token,
        idempotent_replay=idempotent_replay,
    )


def _normalize_message(message: OutboundMessage | Mapping[str, Any] | Any) -> dict[str, Any]:
    if isinstance(message, Mapping):
        raw = dict(message)
    elif is_dataclass(message) and not isinstance(message, type):
        raw = asdict(message)
    elif hasattr(message, "model_dump"):
        raw = message.model_dump(mode="json")
    else:
        names = (
            "idempotency_key",
            "recipient_id",
            "supplier_id",
            "channel",
            "message_type",
            "body",
            "response_token",
            "metadata",
        )
        raw = {name: getattr(message, name) for name in names if hasattr(message, name)}

    required = (
        "idempotency_key",
        "recipient_id",
        "supplier_id",
        "channel",
        "message_type",
        "response_token",
    )
    missing = [name for name in required if not raw.get(name)]
    if missing:
        raise ValueError(f"Outbound message is missing: {', '.join(missing)}")
    raw.setdefault("body", "")
    raw.setdefault("metadata", {})
    if not isinstance(raw["metadata"], Mapping):
        raise TypeError("Outbound message metadata must be a mapping")
    return raw


def _fingerprint(message: Mapping[str, Any]) -> str:
    canonical = json.dumps(
        dict(message),
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("Gateway timestamps must be timezone-aware")
    return value.astimezone(UTC)
