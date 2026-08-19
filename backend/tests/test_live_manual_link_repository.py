from __future__ import annotations

import asyncio
import hashlib
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier

import pytest
from app.live.database import (
    create_database_engine,
    create_schema,
    create_session_factory,
)
from app.live.manual_link_repository import SqlAlchemyManualLinkDeliveryRepository
from app.modules.messaging.gateway import (
    DeliveryState,
    GatewayError,
    GatewayIdempotencyConflict,
    OutboundMessage,
)
from app.modules.messaging.manual_link import (
    ManualDeliveryAction,
    ManualDeliveryActivity,
    ManualLinkDeliveryAdapter,
    ManualLinkDeliveryRecord,
    ManualLinkDeliveryRepository,
)
from sqlalchemy.orm import Session, sessionmaker

NOW = datetime(2026, 8, 19, 18, 0, tzinfo=UTC)
PII_SECRET = "test-only-pii-hash-secret-with-at-least-32-bytes"
RAW_RESPONSE_TOKEN = "signed.internal.response-token-never-persisted"


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _record(
    suffix: str = "alpha-4f14465d8ab04c20a3b29c87d39f41ef",
    *,
    idempotency_key: str | None = None,
    token: str = RAW_RESPONSE_TOKEN,
) -> ManualLinkDeliveryRecord:
    external_id = f"manual_delivery_{suffix}"
    return ManualLinkDeliveryRecord(
        external_id=external_id,
        idempotency_key=idempotency_key or f"rfq:1:{suffix}",
        recipient_id=f"recipient_{suffix}",
        supplier_id=f"supplier_{suffix}",
        message_type="rfq",
        body="Responda a RFQ pelo link individual",
        public_link=f"https://demo.example/supplier/respond/{external_id}",
        response_token_digest=_digest(token),
        metadata={"rfq_round_id": "rfq_1", "tenant_id": "org_demo"},
        fingerprint=_digest(f"fingerprint:{suffix}"),
        status=DeliveryState.SENT_TO_GATEWAY,
        accepted_at=NOW,
    )


def _activity(
    record: ManualLinkDeliveryRecord,
    sequence: int,
    action: ManualDeliveryAction,
    *,
    metadata: dict[str, str] | None = None,
    occurred_at: datetime = NOW,
) -> ManualDeliveryActivity:
    return ManualDeliveryActivity(
        activity_id=f"manual_activity_{record.external_id}_{sequence}",
        external_id=record.external_id,
        action=action,
        actor_id=("system" if action == ManualDeliveryAction.LINK_CREATED else "buyer_1"),
        channel="public_link" if action == ManualDeliveryAction.LINK_CREATED else "email",
        occurred_at=occurred_at,
        metadata=metadata or {},
    )


def _database(
    tmp_path, name: str
) -> tuple[object, sessionmaker[Session], Path]:
    path = tmp_path / name
    engine = create_database_engine(f"sqlite:///{path}")
    create_schema(engine)
    return engine, create_session_factory(engine), path


@pytest.mark.asyncio
async def test_delivery_and_link_created_are_atomic_and_survive_restart(tmp_path) -> None:
    engine, sessions, _ = _database(tmp_path, "manual-link.db")
    record = _record()
    created = _activity(record, 1, ManualDeliveryAction.LINK_CREATED)

    with sessions() as session:
        repository = SqlAlchemyManualLinkDeliveryRepository(
            session, pii_hash_secret=PII_SECRET
        )
        await repository.create(record, created)
        session.commit()
    engine.dispose()

    restarted, restarted_sessions, _ = _database(tmp_path, "manual-link.db")
    with restarted_sessions() as session:
        repository = SqlAlchemyManualLinkDeliveryRepository(
            session, pii_hash_secret=PII_SECRET
        )
        assert await repository.get_by_external_id(record.external_id) == record
        assert await repository.get_by_idempotency_key(record.idempotency_key) == record
        assert (
            await repository.get_by_response_token_digest(record.response_token_digest)
            == record
        )
        assert await repository.list_activities(record.external_id) == (created,)
    restarted.dispose()


@pytest.mark.asyncio
async def test_create_rolls_back_record_if_creation_activity_conflicts(tmp_path) -> None:
    engine, sessions, _ = _database(tmp_path, "atomic-create.db")
    first = _record("first-f24120c5a1494c82a2d8bc9ab6f7ac66")
    second = _record("second-0200a73c714444889230d9d98ed04a2d")
    duplicate_activity_id = "manual_activity_globally_unique"

    with sessions() as session:
        repository = SqlAlchemyManualLinkDeliveryRepository(
            session, pii_hash_secret=PII_SECRET
        )
        first_activity = ManualDeliveryActivity(
            activity_id=duplicate_activity_id,
            external_id=first.external_id,
            action=ManualDeliveryAction.LINK_CREATED,
            actor_id="system",
            channel="public_link",
            occurred_at=NOW,
        )
        await repository.create(first, first_activity)
        session.commit()

        second_activity = ManualDeliveryActivity(
            activity_id=duplicate_activity_id,
            external_id=second.external_id,
            action=ManualDeliveryAction.LINK_CREATED,
            actor_id="system",
            channel="public_link",
            occurred_at=NOW,
        )
        with pytest.raises(GatewayIdempotencyConflict):
            await repository.create(second, second_activity)
        assert await repository.get_by_external_id(second.external_id) is None
        session.commit()
    engine.dispose()


@pytest.mark.asyncio
async def test_unique_delivery_keys_are_enforced_by_database(tmp_path) -> None:
    engine, sessions, _ = _database(tmp_path, "unique-keys.db")
    original = _record("original-1032fe55014848dab6664c1c1a3a9b10")

    with sessions() as session:
        repository = SqlAlchemyManualLinkDeliveryRepository(
            session, pii_hash_secret=PII_SECRET
        )
        await repository.create(
            original, _activity(original, 1, ManualDeliveryAction.LINK_CREATED)
        )
        session.commit()

        conflicts = (
            _record(
                "idem-conflict-f65903e2ae50446dbddabcc034cc964a",
                idempotency_key=original.idempotency_key,
                token="different-token-one",
            ),
            _record(
                "token-conflict-6921b6b0e29f4850acb44c5204271346",
                token=RAW_RESPONSE_TOKEN,
            ),
        )
        for sequence, conflict in enumerate(conflicts, start=2):
            with pytest.raises(GatewayIdempotencyConflict):
                await repository.create(
                    conflict,
                    _activity(
                        conflict,
                        sequence,
                        ManualDeliveryAction.LINK_CREATED,
                    ),
                )
            assert await repository.get_by_external_id(conflict.external_id) is None
    engine.dispose()


@pytest.mark.asyncio
async def test_activities_are_ordered_and_pii_is_hmac_only(tmp_path) -> None:
    engine, sessions, database_path = _database(tmp_path, "activity-pii.db")
    record = _record()
    raw_contact = "supplier.private@example.com"
    raw_ip = "203.0.113.77"
    raw_user_agent = "Supplier Browser with private fingerprint"

    with sessions() as session:
        repository = SqlAlchemyManualLinkDeliveryRepository(
            session, pii_hash_secret=PII_SECRET
        )
        await repository.create(
            record, _activity(record, 1, ManualDeliveryAction.LINK_CREATED)
        )
        await repository.append_activity(
            _activity(
                record,
                2,
                ManualDeliveryAction.LINK_COPIED,
                occurred_at=NOW - timedelta(seconds=5),
            )
        )
        await repository.append_activity(
            _activity(
                record,
                3,
                ManualDeliveryAction.SEND_RECORDED,
                metadata={"recipient_contact": raw_contact},
            )
        )
        delivered = await repository.mark_delivered_on_open(
            record.external_id,
            NOW + timedelta(seconds=1),
            _activity(
                record,
                4,
                ManualDeliveryAction.SUPPLIER_OPENED,
                metadata={"client_ip": raw_ip, "user_agent": raw_user_agent},
                occurred_at=NOW + timedelta(seconds=1),
            ),
        )
        session.commit()

        assert delivered.status == DeliveryState.DELIVERED
        activities = await repository.list_activities(record.external_id)
        # Database insertion order is authoritative even if observed clocks skew.
        assert [activity.action for activity in activities] == [
            ManualDeliveryAction.LINK_CREATED,
            ManualDeliveryAction.LINK_COPIED,
            ManualDeliveryAction.SEND_RECORDED,
            ManualDeliveryAction.SUPPLIER_OPENED,
        ]
        assert set(activities[2].metadata) == {"recipient_contact_hmac_sha256"}
        assert set(activities[3].metadata) == {
            "client_ip_hmac_sha256",
            "user_agent_hmac_sha256",
        }

    engine.dispose()
    database_bytes = database_path.read_bytes()
    for secret_value in (RAW_RESPONSE_TOKEN, raw_contact, raw_ip, raw_user_agent):
        assert secret_value.encode("utf-8") not in database_bytes


@pytest.mark.asyncio
async def test_open_transition_is_idempotent_and_requires_recorded_send(tmp_path) -> None:
    engine, sessions, _ = _database(tmp_path, "idempotent-open.db")
    record = _record()

    with sessions() as session:
        repository = SqlAlchemyManualLinkDeliveryRepository(
            session, pii_hash_secret=PII_SECRET
        )
        await repository.create(
            record, _activity(record, 1, ManualDeliveryAction.LINK_CREATED)
        )
        with pytest.raises(GatewayError, match="manual send is recorded"):
            await repository.mark_delivered_on_open(
                record.external_id,
                NOW,
                _activity(record, 2, ManualDeliveryAction.SUPPLIER_OPENED),
            )

        await repository.append_activity(
            _activity(record, 3, ManualDeliveryAction.SEND_RECORDED)
        )
        first = await repository.mark_delivered_on_open(
            record.external_id,
            NOW,
            _activity(record, 4, ManualDeliveryAction.SUPPLIER_OPENED),
        )
        replay = await repository.mark_delivered_on_open(
            record.external_id,
            NOW + timedelta(minutes=1),
            _activity(record, 5, ManualDeliveryAction.SUPPLIER_OPENED),
        )
        session.commit()

        assert replay == first
        activity = await repository.list_activities(record.external_id)
        assert sum(
            item.action == ManualDeliveryAction.SUPPLIER_OPENED for item in activity
        ) == 1
    engine.dispose()


def test_concurrent_opening_creates_exactly_one_transition_and_activity(tmp_path) -> None:
    engine, sessions, _ = _database(tmp_path, "concurrent-open.db")
    record = _record()
    with sessions() as session:
        repository = SqlAlchemyManualLinkDeliveryRepository(
            session, pii_hash_secret=PII_SECRET
        )
        asyncio.run(
            repository.create(
                record, _activity(record, 1, ManualDeliveryAction.LINK_CREATED)
            )
        )
        asyncio.run(
            repository.append_activity(
                _activity(record, 2, ManualDeliveryAction.SEND_RECORDED)
            )
        )
        session.commit()

    barrier = Barrier(2)

    def open_in_own_transaction(sequence: int) -> ManualLinkDeliveryRecord:
        with sessions() as session:
            repository = SqlAlchemyManualLinkDeliveryRepository(
                session, pii_hash_secret=PII_SECRET
            )
            barrier.wait(timeout=5)
            result = asyncio.run(
                repository.mark_delivered_on_open(
                    record.external_id,
                    NOW + timedelta(seconds=sequence),
                    _activity(
                        record,
                        sequence,
                        ManualDeliveryAction.SUPPLIER_OPENED,
                        occurred_at=NOW + timedelta(seconds=sequence),
                    ),
                )
            )
            session.commit()
            return result

    with ThreadPoolExecutor(max_workers=2) as pool:
        first_future = pool.submit(open_in_own_transaction, 10)
        second_future = pool.submit(open_in_own_transaction, 20)
        results = [first_future.result(timeout=10), second_future.result(timeout=10)]

    assert all(result.status == DeliveryState.DELIVERED for result in results)
    assert results[0].delivered_at == results[1].delivered_at
    with sessions() as session:
        repository = SqlAlchemyManualLinkDeliveryRepository(
            session, pii_hash_secret=PII_SECRET
        )
        activities = asyncio.run(repository.list_activities(record.external_id))
        assert sum(
            item.action == ManualDeliveryAction.SUPPLIER_OPENED for item in activities
        ) == 1
    engine.dispose()


@pytest.mark.asyncio
async def test_repository_rejects_public_link_containing_raw_token(tmp_path) -> None:
    engine, sessions, _ = _database(tmp_path, "reject-token.db")
    safe = _record()
    unsafe = replace(
        safe,
        public_link=f"https://demo.example/supplier/respond?token={RAW_RESPONSE_TOKEN}",
    )
    with sessions() as session:
        repository = SqlAlchemyManualLinkDeliveryRepository(
            session, pii_hash_secret=PII_SECRET
        )
        with pytest.raises(ValueError, match="raw response token"):
            await repository.create(
                unsafe, _activity(unsafe, 1, ManualDeliveryAction.LINK_CREATED)
            )
    engine.dispose()


@pytest.mark.asyncio
async def test_real_adapter_uses_durable_repository_without_persisting_bearer_token(
    tmp_path,
) -> None:
    engine, sessions, database_path = _database(tmp_path, "adapter-integration.db")
    raw_contact = "private-supplier@example.com"
    with sessions() as session:
        repository = SqlAlchemyManualLinkDeliveryRepository(
            session, pii_hash_secret=PII_SECRET
        )
        assert isinstance(repository, ManualLinkDeliveryRepository)
        identifiers = iter(range(1, 20))
        gateway = ManualLinkDeliveryAdapter(
            repository=repository,
            public_base_url="https://demo.example",
            clock=lambda: NOW,
            id_factory=lambda prefix: f"{prefix}_{next(identifiers):032x}",
        )
        sent = await gateway.send(
            OutboundMessage(
                idempotency_key="rfq:adapter:supplier:alpha",
                recipient_id="recipient_alpha",
                supplier_id="supplier_alpha",
                channel="manual_link",
                message_type="rfq",
                body="Responda a RFQ",
                response_token=RAW_RESPONSE_TOKEN,
                metadata={"tenant_id": "org_demo", "rfq_round_id": "rfq_1"},
            )
        )
        public_link = await gateway.get_public_link(sent.external_id)
        assert sent.external_id in public_link
        assert RAW_RESPONSE_TOKEN not in public_link

        await gateway.record_sent(
            sent.external_id,
            actor_id="buyer_1",
            channel="email",
            recipient_contact=raw_contact,
        )
        opened = await gateway.confirm_supplier_open(
            sent.external_id,
            client_ip="203.0.113.88",
            user_agent="Supplier Browser",
        )
        session.commit()
        assert opened.status == DeliveryState.DELIVERED

    engine.dispose()
    database_bytes = database_path.read_bytes()
    assert RAW_RESPONSE_TOKEN.encode() not in database_bytes
    assert raw_contact.encode() not in database_bytes
