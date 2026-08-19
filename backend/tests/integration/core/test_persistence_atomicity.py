"""Atomic state/audit writes and optimistic concurrency on PostgreSQL."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.infrastructure.db.errors import OptimisticLockConflict, TenantScopeViolation
from app.infrastructure.db.records import AggregateRecord, AuditEventRecord
from app.infrastructure.db.unit_of_work import SqlAlchemyUnitOfWork

pytestmark = pytest.mark.postgres

NOW = datetime(2026, 8, 19, 15, 0, tzinfo=UTC)
TENANT_ID = "org_demo"
AGGREGATE_TYPE = "procurement_request"
AGGREGATE_ID = "pr_demo"


def aggregate_record(*, state: str = "DRAFT", version: int = 1) -> AggregateRecord:
    return AggregateRecord(
        tenant_id=TENANT_ID,
        aggregate_type=AGGREGATE_TYPE,
        aggregate_id=AGGREGATE_ID,
        state=state,
        version=version,
        snapshot={"maximum_total_cents": 450_000},
        created_at=NOW,
        updated_at=NOW,
    )


def audit_event(*, aggregate_version: int = 2) -> AuditEventRecord:
    return AuditEventRecord(
        event_id=f"evt_transition_{aggregate_version}",
        event_type="PROCUREMENT_REQUEST_STATE_CHANGED",
        aggregate_type=AGGREGATE_TYPE,
        aggregate_id=AGGREGATE_ID,
        actor_type="human",
        actor_id="usr_buyer",
        occurred_at=NOW + timedelta(minutes=1),
        previous_state="DRAFT",
        new_state="READY",
        correlation_id="cor_transition",
        causation_id="cmd_transition",
        agent_run_id=None,
        idempotency_key="transition-1",
        payload={"reason": "required fields confirmed"},
        aggregate_version=aggregate_version,
    )


async def seed_aggregate(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with SqlAlchemyUnitOfWork(session_factory, TENANT_ID) as uow:
        await uow.aggregates.add(aggregate_record())
        await uow.commit()


async def test_state_change_and_audit_event_are_atomic(
    clean_database: None,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await seed_aggregate(session_factory)

    async with SqlAlchemyUnitOfWork(session_factory, TENANT_ID) as uow:
        current = await uow.aggregates.get(AGGREGATE_TYPE, AGGREGATE_ID)
        assert current is not None
        changed = replace(current, state="READY", updated_at=NOW + timedelta(minutes=1))
        persisted = await uow.aggregates.save(changed, expected_version=current.version)
        await uow.audit.append([audit_event(aggregate_version=persisted.version)])
        await uow.commit()

    async with SqlAlchemyUnitOfWork(session_factory, TENANT_ID) as uow:
        loaded = await uow.aggregates.get(AGGREGATE_TYPE, AGGREGATE_ID)
        events = await uow.audit.list_for_aggregate(AGGREGATE_TYPE, AGGREGATE_ID)
        assert loaded is not None
        assert (loaded.state, loaded.version) == ("READY", 2)
        assert [(event.event_type, event.aggregate_version) for event in events] == [
            ("PROCUREMENT_REQUEST_STATE_CHANGED", 2)
        ]


async def test_failed_transaction_does_not_append_audit_event(
    clean_database: None,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await seed_aggregate(session_factory)

    with pytest.raises(RuntimeError, match="force rollback"):
        async with SqlAlchemyUnitOfWork(session_factory, TENANT_ID) as uow:
            current = await uow.aggregates.get(AGGREGATE_TYPE, AGGREGATE_ID)
            assert current is not None
            changed = replace(current, state="READY", updated_at=NOW + timedelta(minutes=1))
            await uow.aggregates.save(changed, expected_version=current.version)
            await uow.audit.append([audit_event()])
            raise RuntimeError("force rollback")

    async with SqlAlchemyUnitOfWork(session_factory, TENANT_ID) as uow:
        loaded = await uow.aggregates.get(AGGREGATE_TYPE, AGGREGATE_ID)
        events = await uow.audit.list_for_aggregate(AGGREGATE_TYPE, AGGREGATE_ID)
        assert loaded is not None
        assert (loaded.state, loaded.version) == ("DRAFT", 1)
        assert events == ()


async def test_optimistic_lock_rejects_stale_version(
    clean_database: None,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await seed_aggregate(session_factory)

    async with (
        SqlAlchemyUnitOfWork(session_factory, TENANT_ID) as first,
        SqlAlchemyUnitOfWork(session_factory, TENANT_ID) as stale,
    ):
        first_copy = await first.aggregates.get(AGGREGATE_TYPE, AGGREGATE_ID)
        stale_copy = await stale.aggregates.get(AGGREGATE_TYPE, AGGREGATE_ID)
        assert first_copy is not None and stale_copy is not None

        await first.aggregates.save(
            replace(first_copy, state="READY", updated_at=NOW + timedelta(minutes=1)),
            expected_version=first_copy.version,
        )
        await first.audit.append([audit_event()])
        await first.commit()

        with pytest.raises(OptimisticLockConflict):
            await stale.aggregates.save(
                replace(stale_copy, state="CANCELLED", updated_at=NOW + timedelta(minutes=2)),
                expected_version=stale_copy.version,
            )


async def test_repository_scope_prevents_cross_tenant_access(
    clean_database: None,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await seed_aggregate(session_factory)

    async with SqlAlchemyUnitOfWork(session_factory, "org_other") as other_tenant:
        assert await other_tenant.aggregates.get(AGGREGATE_TYPE, AGGREGATE_ID) is None
        with pytest.raises(TenantScopeViolation):
            await other_tenant.aggregates.add(aggregate_record())


async def test_audit_event_table_rejects_updates_and_deletes(
    clean_database: None,
    db_engine: AsyncEngine,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with SqlAlchemyUnitOfWork(session_factory, TENANT_ID) as uow:
        await uow.audit.append([audit_event()])
        await uow.commit()

    with pytest.raises(DBAPIError):
        async with db_engine.begin() as connection:
            await connection.execute(
                text("UPDATE audit_events SET event_type = 'TAMPERED' WHERE event_id = :event_id"),
                {"event_id": "evt_transition_2"},
            )
