"""Outbox acknowledgements are fenced by worker identity and claim generation."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.infrastructure.db.errors import OutboxStateConflict
from app.infrastructure.db.unit_of_work import SqlAlchemyUnitOfWork
from app.infrastructure.outbox.records import OutboxItem

pytestmark = pytest.mark.postgres

NOW = datetime(2026, 8, 19, 15, tzinfo=UTC)
TENANT_ID = "org_demo"


def item() -> OutboxItem:
    return OutboxItem(
        id="out_fenced",
        tenant_id=TENANT_ID,
        kind="SEND_RFQ",
        aggregate_type="procurement_request",
        aggregate_id="pr_demo",
        payload={"recipient_id": "contact_alpha"},
        idempotency_key="rfq:pr_demo:fenced:v1",
        created_at=NOW,
        updated_at=NOW,
    )


async def test_stale_worker_cannot_ack_item_reclaimed_by_new_worker(
    clean_database: None,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with SqlAlchemyUnitOfWork(session_factory, TENANT_ID) as setup:
        await setup.outbox.enqueue(item())
        await setup.commit()

    async with SqlAlchemyUnitOfWork(session_factory, TENANT_ID) as first:
        first_claim = (
            await first.outbox.claim_due(
                worker_id="worker-1", now=NOW, lease_for=timedelta(seconds=30)
            )
        )[0]
        await first.commit()

    reclaimed_at = NOW + timedelta(minutes=1)
    async with SqlAlchemyUnitOfWork(session_factory, TENANT_ID) as second:
        second_claim = (
            await second.outbox.claim_due(
                worker_id="worker-2",
                now=reclaimed_at,
                lease_for=timedelta(seconds=30),
            )
        )[0]
        await second.commit()

    async with SqlAlchemyUnitOfWork(session_factory, TENANT_ID) as stale:
        with pytest.raises(OutboxStateConflict, match="STALE_OR_FOREIGN_LEASE"):
            await stale.outbox.mark_delivered(
                first_claim.id,
                worker_id="worker-1",
                attempt_count=first_claim.attempt_count,
                external_delivery_id="provider-stale",
                delivered_at=reclaimed_at + timedelta(seconds=1),
            )

    async with SqlAlchemyUnitOfWork(session_factory, TENANT_ID) as owner:
        delivered = await owner.outbox.mark_delivered(
            second_claim.id,
            worker_id="worker-2",
            attempt_count=second_claim.attempt_count,
            external_delivery_id="provider-current",
            delivered_at=reclaimed_at + timedelta(seconds=1),
        )
        assert delivered.external_delivery_id == "provider-current"
        await owner.commit()
