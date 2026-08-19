"""Idempotency registry, nonce consumption and outbox retry semantics."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.contracts import OutboxStatus
from app.infrastructure.db.errors import IdempotencyConflict
from app.infrastructure.db.models import OutboxItemModel
from app.infrastructure.db.records import ConsumedLinkNonce
from app.infrastructure.db.unit_of_work import SqlAlchemyUnitOfWork
from app.infrastructure.outbox.records import OutboxItem

pytestmark = pytest.mark.postgres

NOW = datetime(2026, 8, 19, 15, 0, tzinfo=UTC)
TENANT_ID = "org_demo"


def outbox_item(*, payload_text: str = "hello") -> OutboxItem:
    return OutboxItem(
        id="out_message_1",
        tenant_id=TENANT_ID,
        kind="SEND_RFQ",
        aggregate_type="procurement_request",
        aggregate_id="pr_demo",
        payload={"body": payload_text, "recipient_id": "contact_alpha"},
        idempotency_key="rfq:pr_demo:contact_alpha:v1",
        created_at=NOW,
        updated_at=NOW,
    )


async def test_same_idempotency_key_and_payload_returns_original_result(
    clean_database: None,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with SqlAlchemyUnitOfWork(session_factory, TENANT_ID) as uow:
        claim = await uow.idempotency.reserve(
            operation="create_rfq",
            idempotency_key="idem-1",
            request_payload={"people_count": 80, "maximum_total_cents": 450_000},
            now=NOW,
        )
        assert claim.claimed
        await uow.idempotency.complete(
            operation="create_rfq",
            idempotency_key="idem-1",
            outcome_kind="SUCCESS",
            response_payload={"round_id": "rfq_1"},
            completed_at=NOW,
        )
        await uow.commit()

    async with SqlAlchemyUnitOfWork(session_factory, TENANT_ID) as replay_uow:
        replay = await replay_uow.idempotency.reserve(
            operation="create_rfq",
            idempotency_key="idem-1",
            request_payload={"maximum_total_cents": 450_000, "people_count": 80},
            now=NOW + timedelta(seconds=1),
        )
        assert not replay.claimed
        assert replay.response_payload == {"round_id": "rfq_1"}


async def test_same_idempotency_key_with_different_payload_is_rejected(
    clean_database: None,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with SqlAlchemyUnitOfWork(session_factory, TENANT_ID) as uow:
        await uow.idempotency.reserve(
            operation="create_rfq",
            idempotency_key="idem-conflict",
            request_payload={"people_count": 80},
            now=NOW,
        )
        await uow.idempotency.complete(
            operation="create_rfq",
            idempotency_key="idem-conflict",
            outcome_kind="SUCCESS",
            response_payload={"round_id": "rfq_1"},
            completed_at=NOW,
        )
        await uow.commit()

    async with SqlAlchemyUnitOfWork(session_factory, TENANT_ID) as conflicting_uow:
        with pytest.raises(IdempotencyConflict):
            await conflicting_uow.idempotency.reserve(
                operation="create_rfq",
                idempotency_key="idem-conflict",
                request_payload={"people_count": 81},
                now=NOW,
            )


async def test_rolled_back_idempotency_reservation_can_be_retried(
    clean_database: None,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    with pytest.raises(RuntimeError, match="transient"):
        async with SqlAlchemyUnitOfWork(session_factory, TENANT_ID) as uow:
            await uow.idempotency.reserve(
                operation="send_award",
                idempotency_key="award-1",
                request_payload={"award_id": "awd_1"},
                now=NOW,
            )
            raise RuntimeError("transient")

    async with SqlAlchemyUnitOfWork(session_factory, TENANT_ID) as retry_uow:
        claim = await retry_uow.idempotency.reserve(
            operation="send_award",
            idempotency_key="award-1",
            request_payload={"award_id": "awd_1"},
            now=NOW + timedelta(seconds=1),
        )
        assert claim.claimed


async def test_outbox_retry_does_not_duplicate_business_event(
    clean_database: None,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with SqlAlchemyUnitOfWork(session_factory, TENANT_ID) as uow:
        first, inserted = await uow.outbox.enqueue(outbox_item())
        replay, replay_inserted = await uow.outbox.enqueue(outbox_item())
        assert inserted and not replay_inserted
        assert first.id == replay.id
        await uow.commit()

    async with SqlAlchemyUnitOfWork(session_factory, TENANT_ID) as worker:
        claimed = await worker.outbox.claim_due(
            worker_id="worker-1",
            now=NOW,
            lease_for=timedelta(seconds=30),
        )
        assert len(claimed) == 1
        assert claimed[0].attempt_count == 1
        await worker.outbox.mark_failed(
            claimed[0].id,
            error="gateway unavailable",
            next_attempt_at=NOW + timedelta(minutes=1),
            now=NOW,
        )
        await worker.commit()

    async with SqlAlchemyUnitOfWork(session_factory, TENANT_ID) as retry_worker:
        retried = await retry_worker.outbox.claim_due(
            worker_id="worker-2",
            now=NOW + timedelta(minutes=2),
            lease_for=timedelta(seconds=30),
        )
        assert len(retried) == 1
        assert retried[0].attempt_count == 2
        delivered = await retry_worker.outbox.mark_delivered(
            retried[0].id,
            external_delivery_id="provider-ack-1",
            delivered_at=NOW + timedelta(minutes=2),
        )
        assert delivered.status is OutboxStatus.DELIVERED
        await retry_worker.commit()

    async with SqlAlchemyUnitOfWork(session_factory, TENANT_ID) as verification:
        assert not await verification.outbox.claim_due(
            worker_id="worker-3",
            now=NOW + timedelta(hours=1),
            lease_for=timedelta(seconds=30),
        )
        repeated_ack = await verification.outbox.mark_delivered(
            "out_message_1",
            external_delivery_id="provider-ack-1",
            delivered_at=NOW + timedelta(hours=1),
        )
        assert repeated_ack.attempt_count == 2
        count = await verification.session.scalar(select(func.count()).select_from(OutboxItemModel))
        assert count == 1


async def test_outbox_workers_skip_rows_locked_by_another_worker(
    clean_database: None,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with SqlAlchemyUnitOfWork(session_factory, TENANT_ID) as uow:
        await uow.outbox.enqueue(outbox_item())
        await uow.commit()

    async with SqlAlchemyUnitOfWork(session_factory, TENANT_ID) as first_worker:
        first_claim = await first_worker.outbox.claim_due(
            worker_id="worker-1",
            now=NOW,
            lease_for=timedelta(seconds=30),
        )
        assert len(first_claim) == 1
        async with SqlAlchemyUnitOfWork(session_factory, TENANT_ID) as second_worker:
            second_claim = await second_worker.outbox.claim_due(
                worker_id="worker-2",
                now=NOW,
                lease_for=timedelta(seconds=30),
            )
            assert second_claim == ()
        await first_worker.commit()


async def test_outbox_key_rejects_different_payload(
    clean_database: None,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with SqlAlchemyUnitOfWork(session_factory, TENANT_ID) as uow:
        await uow.outbox.enqueue(outbox_item())
        await uow.commit()

    async with SqlAlchemyUnitOfWork(session_factory, TENANT_ID) as conflict_uow:
        with pytest.raises(IdempotencyConflict):
            await conflict_uow.outbox.enqueue(outbox_item(payload_text="changed"))


async def test_nonce_consumption_is_atomic_and_single_use(
    clean_database: None,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    nonce = ConsumedLinkNonce(
        tenant_id=TENANT_ID,
        purpose="supplier_profile_review",
        nonce_hash="a" * 64,
        subject_id="sup_alpha",
        expires_at=NOW + timedelta(hours=1),
        consumed_at=NOW,
    )
    async with SqlAlchemyUnitOfWork(session_factory, TENANT_ID) as uow:
        assert await uow.nonces.consume(nonce)
        await uow.commit()

    async with SqlAlchemyUnitOfWork(session_factory, TENANT_ID) as replay_uow:
        assert not await replay_uow.nonces.consume(nonce)
