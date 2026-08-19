"""Explicit tenant-scoped unit of work for atomic state, audit and outbox writes."""

from __future__ import annotations

from types import TracebackType

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.infrastructure.outbox.repository import OutboxRepository

from .repositories import (
    AggregateRepository,
    AuditEventRepository,
    ConsumedLinkNonceRepository,
    IdempotencyRepository,
)


class SqlAlchemyUnitOfWork:
    """Own one SQLAlchemy session; callers must explicitly commit successful work."""

    aggregates: AggregateRepository
    audit: AuditEventRepository
    idempotency: IdempotencyRepository
    nonces: ConsumedLinkNonceRepository
    outbox: OutboxRepository

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        tenant_id: str,
    ) -> None:
        if not tenant_id:
            raise ValueError("tenant_id cannot be empty")
        self._session_factory = session_factory
        self._tenant_id = tenant_id
        self._session: AsyncSession | None = None
        self._committed = False

    @property
    def tenant_id(self) -> str:
        return self._tenant_id

    @property
    def session(self) -> AsyncSession:
        if self._session is None:
            raise RuntimeError("unit of work has not been entered")
        return self._session

    async def __aenter__(self) -> SqlAlchemyUnitOfWork:
        if self._session is not None:
            raise RuntimeError("unit of work cannot be entered twice")
        self._session = self._session_factory()
        self._committed = False
        self.aggregates = AggregateRepository(self._session, self._tenant_id)
        self.audit = AuditEventRepository(self._session, self._tenant_id)
        self.idempotency = IdempotencyRepository(self._session, self._tenant_id)
        self.nonces = ConsumedLinkNonceRepository(self._session, self._tenant_id)
        self.outbox = OutboxRepository(self._session, self._tenant_id)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_value, traceback
        session = self.session
        try:
            if exc_type is not None or not self._committed:
                await session.rollback()
        finally:
            await session.close()
            self._session = None

    async def commit(self) -> None:
        await self.session.commit()
        self._committed = True

    async def rollback(self) -> None:
        await self.session.rollback()
        self._committed = False
