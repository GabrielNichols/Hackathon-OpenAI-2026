from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, datetime
from urllib.parse import urlsplit

import pytest

from app.modules.messaging.gateway import (
    DeliveryState,
    GatewayError,
    GatewayIdempotencyConflict,
    GatewayMessageNotFound,
    OutboundMessage,
)
from app.modules.messaging.manual_link import (
    ManualDeliveryAction,
    ManualDeliveryActivity,
    ManualLinkDeliveryAdapter,
    ManualLinkDeliveryRecord,
)

NOW = datetime(2026, 8, 19, 18, 0, tzinfo=UTC)


class InMemoryManualLinkRepositoryForTests:
    """Test double only; runtime must inject durable persistence."""

    def __init__(self) -> None:
        self.records: dict[str, ManualLinkDeliveryRecord] = {}
        self.by_idempotency_key: dict[str, str] = {}
        self.by_token_digest: dict[str, str] = {}
        self.activities: dict[str, list[ManualDeliveryActivity]] = {}
        self._lock = asyncio.Lock()

    async def get_by_external_id(
        self, external_id: str
    ) -> ManualLinkDeliveryRecord | None:
        return self.records.get(external_id)

    async def get_by_idempotency_key(
        self, idempotency_key: str
    ) -> ManualLinkDeliveryRecord | None:
        external_id = self.by_idempotency_key.get(idempotency_key)
        return self.records.get(external_id) if external_id else None

    async def get_by_response_token_digest(
        self, response_token_digest: str
    ) -> ManualLinkDeliveryRecord | None:
        external_id = self.by_token_digest.get(response_token_digest)
        return self.records.get(external_id) if external_id else None

    async def list_records(self) -> Sequence[ManualLinkDeliveryRecord]:
        return tuple(self.records.values())

    async def create(
        self,
        record: ManualLinkDeliveryRecord,
        activity: ManualDeliveryActivity,
    ) -> None:
        async with self._lock:
            if record.external_id in self.records:
                raise AssertionError("duplicate external_id")
            if record.idempotency_key in self.by_idempotency_key:
                raise AssertionError("duplicate idempotency_key")
            if record.response_token_digest in self.by_token_digest:
                raise AssertionError("duplicate response token")
            self.records[record.external_id] = record
            self.by_idempotency_key[record.idempotency_key] = record.external_id
            self.by_token_digest[record.response_token_digest] = record.external_id
            self.activities[record.external_id] = [activity]

    async def append_activity(self, activity: ManualDeliveryActivity) -> None:
        async with self._lock:
            self.activities[activity.external_id].append(activity)

    async def has_activity(
        self,
        external_id: str,
        action: ManualDeliveryAction,
    ) -> bool:
        return any(item.action == action for item in self.activities.get(external_id, []))

    async def list_activities(
        self, external_id: str
    ) -> Sequence[ManualDeliveryActivity]:
        return tuple(self.activities.get(external_id, []))

    async def mark_delivered_on_open(
        self,
        external_id: str,
        delivered_at: datetime,
        activity: ManualDeliveryActivity,
    ) -> ManualLinkDeliveryRecord:
        async with self._lock:
            current = self.records[external_id]
            if current.status == DeliveryState.DELIVERED:
                return current
            delivered = replace(
                current,
                status=DeliveryState.DELIVERED,
                delivered_at=delivered_at,
            )
            self.records[external_id] = delivered
            self.activities[external_id].append(activity)
            return delivered


def message(
    *,
    idempotency_key: str = "rfq:1:supplier:alpha",
    supplier_id: str = "supplier_alpha",
    response_token: str = "signed.token-alpha",
) -> OutboundMessage:
    return OutboundMessage(
        idempotency_key=idempotency_key,
        recipient_id=f"recipient_{supplier_id}",
        supplier_id=supplier_id,
        channel="manual_link",
        message_type="rfq",
        body="Responda à RFQ pelo link individual",
        response_token=response_token,
        metadata={"rfq_round_id": "rfq_1", "tenant_id": "org_demo"},
    )


def adapter(
    repository: InMemoryManualLinkRepositoryForTests,
) -> ManualLinkDeliveryAdapter:
    sequence = iter(range(1, 100))
    return ManualLinkDeliveryAdapter(
        repository=repository,
        public_base_url="https://procurement-demo.example",
        clock=lambda: NOW,
        id_factory=lambda prefix: f"{prefix}_{next(sequence)}",
    )


def test_adapter_requires_public_https_origin():
    repository = InMemoryManualLinkRepositoryForTests()

    with pytest.raises(ValueError, match="absolute HTTPS origin"):
        ManualLinkDeliveryAdapter(
            repository=repository,
            public_base_url="http://localhost:8000",
        )
    with pytest.raises(ValueError, match="origin without a path"):
        ManualLinkDeliveryAdapter(
            repository=repository,
            public_base_url="https://demo.example/internal",
        )


@pytest.mark.asyncio
async def test_send_generates_individual_absolute_link_without_claiming_delivery():
    repository = InMemoryManualLinkRepositoryForTests()
    gateway = adapter(repository)

    alpha = await gateway.send(message(response_token="alpha.secret&part"))
    beta = await gateway.send(
        message(
            idempotency_key="rfq:1:supplier:beta",
            supplier_id="supplier_beta",
            response_token="beta.secret",
        )
    )
    replay = await gateway.send(message(response_token="alpha.secret&part"))

    alpha_link = await gateway.get_public_link(alpha.external_id)
    parsed = urlsplit(alpha_link)
    assert parsed.scheme == "https"
    assert parsed.netloc == "procurement-demo.example"
    assert parsed.path == f"/live/supplier/rfq/{alpha.external_id}"
    assert parsed.query == ""
    assert "alpha.secret" not in alpha_link
    assert alpha_link != await gateway.get_public_link(beta.external_id)
    assert alpha.status == DeliveryState.SENT_TO_GATEWAY
    assert (await gateway.get_status(alpha.external_id)).delivered_at is None
    assert replay.external_id == alpha.external_id
    assert replay.idempotent_replay is True


@pytest.mark.asyncio
async def test_copy_and_manual_send_are_explicit_but_never_mark_delivered():
    repository = InMemoryManualLinkRepositoryForTests()
    gateway = adapter(repository)
    sent = await gateway.send(message())

    copied = await gateway.record_link_copied(
        sent.external_id,
        actor_id="buyer_gabriel",
        channel="whatsapp",
    )
    recorded = await gateway.record_sent(
        sent.external_id,
        actor_id="buyer_gabriel",
        channel="whatsapp",
        recipient_contact="+55 11 99999-0000",
    )

    status = await gateway.get_status(sent.external_id)
    assert copied.action == ManualDeliveryAction.LINK_COPIED
    assert recorded.action == ManualDeliveryAction.SEND_RECORDED
    assert recorded.actor_id == "buyer_gabriel"
    assert recorded.channel == "whatsapp"
    assert recorded.metadata["recipient_contact"] == "+55 11 99999-0000"
    assert status.status == DeliveryState.SENT_TO_GATEWAY
    assert status.delivered_at is None


@pytest.mark.asyncio
async def test_only_supplier_link_open_confirms_delivery_and_is_idempotent():
    repository = InMemoryManualLinkRepositoryForTests()
    gateway = adapter(repository)
    sent = await gateway.send(message())

    with pytest.raises(GatewayError, match="manual send is recorded"):
        await gateway.confirm_supplier_open(sent.external_id)

    await gateway.record_sent(
        sent.external_id,
        actor_id="buyer_gabriel",
        channel="email",
        recipient_contact="supplier@example.com",
    )
    opened = await gateway.confirm_supplier_open(
        sent.external_id,
        client_ip="203.0.113.10",
        user_agent="Supplier Browser",
    )
    replay = await gateway.confirm_supplier_open(sent.external_id)

    assert opened.status == DeliveryState.DELIVERED
    assert opened.delivered_at == NOW
    assert replay == opened
    activity = await gateway.get_activity(sent.external_id)
    assert [item.action for item in activity] == [
        ManualDeliveryAction.LINK_CREATED,
        ManualDeliveryAction.SEND_RECORDED,
        ManualDeliveryAction.SUPPLIER_OPENED,
    ]
    assert activity[-1].actor_id == "supplier_alpha"
    assert activity[-1].metadata == {
        "client_ip": "203.0.113.10",
        "user_agent": "Supplier Browser",
    }


@pytest.mark.asyncio
async def test_unknown_token_and_idempotency_conflict_are_rejected():
    repository = InMemoryManualLinkRepositoryForTests()
    gateway = adapter(repository)
    await gateway.send(message())

    with pytest.raises(GatewayMessageNotFound, match="No manual delivery"):
        await gateway.confirm_supplier_open("wrong-capability")
    with pytest.raises(GatewayIdempotencyConflict):
        await gateway.send(message(supplier_id="supplier_beta"))
