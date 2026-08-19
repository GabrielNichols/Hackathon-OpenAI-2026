"""Signed-link replay protection uses the caller-owned PostgreSQL UoW."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.infrastructure.db.unit_of_work import SqlAlchemyUnitOfWork
from app.infrastructure.security import SignedLinkError, SignedLinkPayload, SignedLinkService

pytestmark = pytest.mark.postgres

NOW = datetime(2026, 8, 19, 15, tzinfo=UTC)
TENANT_ID = "org_demo"
SERVICE = SignedLinkService(b"test-secret-with-at-least-32-bytes!!")


async def test_verify_and_consume_uses_durable_uow_nonce_registry(
    clean_database: None,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    token = SERVICE.issue(
        SignedLinkPayload(
            purpose="supplier_profile_review",
            subject_id="sup_alpha",
            recipient_id="contact_alpha",
            expires_at=NOW + timedelta(hours=1),
            nonce="nonce-postgres-1",
            tenant_id=TENANT_ID,
        )
    )
    async with SqlAlchemyUnitOfWork(session_factory, TENANT_ID) as first:
        payload = await SERVICE.verify_and_consume(
            token,
            registry=first.nonces,
            expected_purpose="supplier_profile_review",
            expected_tenant_id=TENANT_ID,
            now=NOW,
        )
        assert payload.subject_id == "sup_alpha"
        await first.commit()

    async with SqlAlchemyUnitOfWork(session_factory, TENANT_ID) as replay:
        with pytest.raises(SignedLinkError, match="already consumed"):
            await SERVICE.verify_and_consume(
                token,
                registry=replay.nonces,
                expected_purpose="supplier_profile_review",
                expected_tenant_id=TENANT_ID,
                now=NOW + timedelta(seconds=1),
            )
