"""The UoW refuses aggregate transitions without matching audit facts."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.infrastructure.db.errors import AuditInvariantViolation
from app.infrastructure.db.records import AggregateRecord
from app.infrastructure.db.unit_of_work import SqlAlchemyUnitOfWork

pytestmark = pytest.mark.postgres

NOW = datetime(2026, 8, 19, 15, tzinfo=UTC)
TENANT_ID = "org_demo"


def record() -> AggregateRecord:
    return AggregateRecord(
        tenant_id=TENANT_ID,
        aggregate_type="procurement_request",
        aggregate_id="pr_audit_guard",
        state="DRAFT",
        version=1,
        snapshot={"people_count": 80},
        created_at=NOW,
        updated_at=NOW,
    )


async def test_commit_rejects_state_change_without_matching_audit_event(
    clean_database: None,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with SqlAlchemyUnitOfWork(session_factory, TENANT_ID) as seed:
        await seed.aggregates.add(record())
        await seed.commit()

    async with SqlAlchemyUnitOfWork(session_factory, TENANT_ID) as uow:
        current = await uow.aggregates.get("procurement_request", "pr_audit_guard")
        assert current is not None
        await uow.aggregates.save(
            replace(current, state="READY", updated_at=NOW + timedelta(minutes=1)),
            expected_version=current.version,
        )
        with pytest.raises(AuditInvariantViolation):
            await uow.commit()

    async with SqlAlchemyUnitOfWork(session_factory, TENANT_ID) as verification:
        persisted = await verification.aggregates.get("procurement_request", "pr_audit_guard")
        assert persisted is not None
        assert (persisted.state, persisted.version) == ("DRAFT", 1)
