"""Real manual-link delivery adapter for the hackathon golden path.

This adapter does not claim to send a message.  It prepares an individual
public link, records the human copy/send actions and only confirms delivery
when the supplier opens that exact opaque-capability link.  The internal signed
workflow token is never placed in the public URL.

Persistence is deliberately represented by a protocol.  Runtime composition
must inject a durable implementation; an in-memory implementation belongs in
tests only.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable
from urllib.parse import quote, urlsplit, urlunsplit
from uuid import uuid4

from app.modules.messaging.gateway import (
    DeliveryState,
    GatewayDeliveryStatus,
    GatewayError,
    GatewayIdempotencyConflict,
    GatewayMessageNotFound,
    GatewaySendResult,
    OutboundMessage,
)


class ManualDeliveryAction(StrEnum):
    """Auditable actions in a manual delivery."""

    LINK_CREATED = "LINK_CREATED"
    LINK_COPIED = "LINK_COPIED"
    SEND_RECORDED = "SEND_RECORDED"
    SUPPLIER_OPENED = "SUPPLIER_OPENED"


class ManualDeliveryChannel(StrEnum):
    """Out-of-band channels a human may actually use."""

    EMAIL = "email"
    WHATSAPP = "whatsapp"
    SMS = "sms"
    OTHER = "other"


@dataclass(frozen=True, slots=True)
class ManualLinkDeliveryRecord:
    """Durable state for one individualized manual delivery."""

    external_id: str
    idempotency_key: str
    recipient_id: str
    supplier_id: str
    message_type: str
    body: str
    public_link: str
    response_token_digest: str
    metadata: Mapping[str, Any]
    fingerprint: str
    status: DeliveryState
    accepted_at: datetime
    delivered_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class ManualDeliveryActivity:
    """Append-only evidence of a human or supplier delivery action."""

    activity_id: str
    external_id: str
    action: ManualDeliveryAction
    actor_id: str
    channel: str
    occurred_at: datetime
    metadata: Mapping[str, Any] = field(default_factory=dict)


@runtime_checkable
class ManualLinkDeliveryRepository(Protocol):
    """Persistence contract required by :class:`ManualLinkDeliveryAdapter`.

    ``create`` and ``mark_delivered_on_open`` must be atomic in durable
    implementations.  The latter must remain idempotent under concurrent
    requests for the same link.
    """

    async def get_by_external_id(
        self, external_id: str
    ) -> ManualLinkDeliveryRecord | None: ...

    async def get_by_idempotency_key(
        self, idempotency_key: str
    ) -> ManualLinkDeliveryRecord | None: ...

    async def get_by_response_token_digest(
        self, response_token_digest: str
    ) -> ManualLinkDeliveryRecord | None: ...

    async def list_records(self) -> Sequence[ManualLinkDeliveryRecord]: ...

    async def create(
        self,
        record: ManualLinkDeliveryRecord,
        activity: ManualDeliveryActivity,
    ) -> None: ...

    async def append_activity(self, activity: ManualDeliveryActivity) -> None: ...

    async def has_activity(
        self,
        external_id: str,
        action: ManualDeliveryAction,
    ) -> bool: ...

    async def list_activities(
        self, external_id: str
    ) -> Sequence[ManualDeliveryActivity]: ...

    async def mark_delivered_on_open(
        self,
        external_id: str,
        delivered_at: datetime,
        activity: ManualDeliveryActivity,
    ) -> ManualLinkDeliveryRecord: ...


class ManualLinkDeliveryAdapter:
    """Prepare and audit real manual delivery through a public supplier link.

    ``send`` means only "the link is ready to be sent manually".  Neither
    ``send`` nor the human audit methods return ``DELIVERED``.  Delivery is
    confirmed exclusively by :meth:`confirm_supplier_open`.
    """

    def __init__(
        self,
        *,
        repository: ManualLinkDeliveryRepository,
        public_base_url: str,
        supplier_rfq_path: str = "/live/supplier/rfq",
        supplier_award_path: str = "/live/supplier/awards",
        clock: Callable[[], datetime] | Any | None = None,
        id_factory: Callable[[str], str] | None = None,
    ) -> None:
        self._repository = repository
        self._public_origin = _validate_public_origin(public_base_url)
        self._supplier_paths = {
            "rfq": _validate_response_path(supplier_rfq_path),
            "award": _validate_response_path(supplier_award_path),
        }
        self._clock = clock
        self._id_factory = id_factory or _uuid_id

    async def send(
        self,
        message: OutboundMessage | Mapping[str, Any] | Any,
    ) -> GatewaySendResult:
        """Create a public link without claiming human send or delivery."""

        normalized = _normalize_message(message)
        if normalized["channel"] != "manual_link":
            raise ValueError("ManualLinkDeliveryAdapter accepts only channel='manual_link'")

        fingerprint = _fingerprint(normalized)
        existing = await self._repository.get_by_idempotency_key(
            normalized["idempotency_key"]
        )
        if existing is not None:
            if existing.fingerprint != fingerprint:
                raise GatewayIdempotencyConflict(
                    "The idempotency key is already bound to another manual delivery"
                )
            return GatewaySendResult(
                external_id=existing.external_id,
                status=existing.status,
                accepted_at=existing.accepted_at,
                response_token=normalized["response_token"],
                idempotent_replay=True,
            )

        now = self._now()
        external_id = self._id_factory("manual_delivery")
        public_link = self._build_public_link(external_id, normalized["message_type"])
        record = ManualLinkDeliveryRecord(
            external_id=external_id,
            idempotency_key=normalized["idempotency_key"],
            recipient_id=normalized["recipient_id"],
            supplier_id=normalized["supplier_id"],
            message_type=normalized["message_type"],
            body=normalized["body"],
            public_link=public_link,
            response_token_digest=_token_digest(normalized["response_token"]),
            metadata=dict(normalized["metadata"]),
            fingerprint=fingerprint,
            status=DeliveryState.SENT_TO_GATEWAY,
            accepted_at=now,
        )
        activity = self._activity(
            external_id=external_id,
            action=ManualDeliveryAction.LINK_CREATED,
            actor_id="system",
            channel="public_link",
            metadata={"message_type": record.message_type},
            occurred_at=now,
        )
        await self._repository.create(record, activity)
        return GatewaySendResult(
            external_id=external_id,
            status=DeliveryState.SENT_TO_GATEWAY,
            accepted_at=now,
            response_token=normalized["response_token"],
        )

    async def get_status(self, external_id: str) -> GatewayDeliveryStatus:
        record = await self._required_record(external_id)
        return GatewayDeliveryStatus(
            external_id=record.external_id,
            status=record.status,
            delivered_at=record.delivered_at,
        )

    async def get_public_link(self, external_id: str) -> str:
        """Return the individualized link for the authenticated buyer UI."""

        return (await self._required_record(external_id)).public_link

    async def record_link_copied(
        self,
        external_id: str,
        *,
        actor_id: str,
        channel: ManualDeliveryChannel | str,
    ) -> ManualDeliveryActivity:
        """Audit who copied a link and the intended real delivery channel."""

        record = await self._required_pending_record(external_id)
        activity = self._activity(
            external_id=record.external_id,
            action=ManualDeliveryAction.LINK_COPIED,
            actor_id=actor_id,
            channel=_manual_channel(channel),
        )
        await self._repository.append_activity(activity)
        return activity

    async def record_sent(
        self,
        external_id: str,
        *,
        actor_id: str,
        channel: ManualDeliveryChannel | str,
        recipient_contact: str,
    ) -> ManualDeliveryActivity:
        """Audit a human-confirmed out-of-band send without marking delivery."""

        record = await self._required_pending_record(external_id)
        if not recipient_contact.strip():
            raise ValueError("recipient_contact must not be empty")
        activity = self._activity(
            external_id=record.external_id,
            action=ManualDeliveryAction.SEND_RECORDED,
            actor_id=actor_id,
            channel=_manual_channel(channel),
            metadata={"recipient_contact": recipient_contact.strip()},
        )
        await self._repository.append_activity(activity)
        return activity

    async def confirm_supplier_open(
        self,
        public_capability: str,
        *,
        client_ip: str | None = None,
        user_agent: str | None = None,
    ) -> GatewayDeliveryStatus:
        """Confirm delivery from the supplier's opaque-capability page opening.

        A prior human ``record_sent`` event is required.  This prevents runtime
        code from bypassing the PRD's explicit manual-send audit requirement.
        """

        if not public_capability:
            raise GatewayMessageNotFound("No manual delivery matches this supplier link")
        record = await self._repository.get_by_external_id(public_capability)
        if record is None:
            raise GatewayMessageNotFound("No manual delivery matches this supplier link")
        if record.status == DeliveryState.DELIVERED:
            return GatewayDeliveryStatus(
                external_id=record.external_id,
                status=record.status,
                delivered_at=record.delivered_at,
            )
        was_sent = await self._repository.has_activity(
            record.external_id,
            ManualDeliveryAction.SEND_RECORDED,
        )
        if not was_sent:
            raise GatewayError(
                "Supplier delivery cannot be confirmed before the manual send is recorded"
            )

        delivered_at = self._now()
        evidence = {
            key: value
            for key, value in {"client_ip": client_ip, "user_agent": user_agent}.items()
            if value
        }
        activity = self._activity(
            external_id=record.external_id,
            action=ManualDeliveryAction.SUPPLIER_OPENED,
            actor_id=record.supplier_id,
            channel="public_link",
            metadata=evidence,
            occurred_at=delivered_at,
        )
        delivered = await self._repository.mark_delivered_on_open(
            record.external_id,
            delivered_at,
            activity,
        )
        return GatewayDeliveryStatus(
            external_id=delivered.external_id,
            status=delivered.status,
            delivered_at=delivered.delivered_at,
        )

    async def get_activity(self, external_id: str) -> Sequence[ManualDeliveryActivity]:
        """Expose the append-only trail to an authenticated operations UI."""

        await self._required_record(external_id)
        return await self._repository.list_activities(external_id)

    async def _required_record(self, external_id: str) -> ManualLinkDeliveryRecord:
        record = await self._repository.get_by_external_id(external_id)
        if record is None:
            raise GatewayMessageNotFound(f"Unknown gateway external_id: {external_id}")
        return record

    async def _required_pending_record(self, external_id: str) -> ManualLinkDeliveryRecord:
        record = await self._required_record(external_id)
        if record.status == DeliveryState.DELIVERED:
            raise GatewayError("This manual delivery is already confirmed")
        return record

    def _build_public_link(self, public_capability: str, message_type: str) -> str:
        try:
            response_path = self._supplier_paths[message_type]
        except KeyError as error:
            raise ValueError(
                "ManualLinkDeliveryAdapter supports only rfq and award messages"
            ) from error
        origin = urlsplit(self._public_origin)
        return urlunsplit(
            (
                origin.scheme,
                origin.netloc,
                f"{response_path.rstrip('/')}/{quote(public_capability, safe='')}",
                "",
                "",
            )
        )

    def _activity(
        self,
        *,
        external_id: str,
        action: ManualDeliveryAction,
        actor_id: str,
        channel: str,
        metadata: Mapping[str, Any] | None = None,
        occurred_at: datetime | None = None,
    ) -> ManualDeliveryActivity:
        if not actor_id.strip():
            raise ValueError("actor_id must not be empty")
        return ManualDeliveryActivity(
            activity_id=self._id_factory("manual_activity"),
            external_id=external_id,
            action=action,
            actor_id=actor_id.strip(),
            channel=channel,
            occurred_at=occurred_at or self._now(),
            metadata=dict(metadata or {}),
        )

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


def _validate_public_origin(value: str) -> str:
    parsed = urlsplit(value.strip())
    if parsed.scheme != "https" or not parsed.netloc:
        raise ValueError("public_base_url must be an absolute HTTPS origin")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("public_base_url must not contain credentials, query or fragment")
    if parsed.path not in {"", "/"}:
        raise ValueError("public_base_url must be an origin without a path")
    return urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))


def _validate_response_path(value: str) -> str:
    if not value.startswith("/") or value.startswith("//"):
        raise ValueError("supplier_response_path must be an absolute application path")
    parsed = urlsplit(value)
    if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment:
        raise ValueError("supplier_response_path must not contain a URL origin or query")
    return parsed.path


def _manual_channel(value: ManualDeliveryChannel | str) -> str:
    try:
        return ManualDeliveryChannel(value).value
    except ValueError as error:
        allowed = ", ".join(channel.value for channel in ManualDeliveryChannel)
        raise ValueError(f"Manual delivery channel must be one of: {allowed}") from error


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


def _token_digest(response_token: str) -> str:
    return hashlib.sha256(response_token.encode("utf-8")).hexdigest()


def _uuid_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Manual delivery timestamps must be timezone-aware")
    return value.astimezone(UTC)


__all__ = [
    "ManualDeliveryAction",
    "ManualDeliveryActivity",
    "ManualDeliveryChannel",
    "ManualLinkDeliveryAdapter",
    "ManualLinkDeliveryRecord",
    "ManualLinkDeliveryRepository",
]
