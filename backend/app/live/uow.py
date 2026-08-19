"""Synchronous SQLAlchemy unit of work for Dev 4 command boundaries."""

from __future__ import annotations

from types import TracebackType

from sqlalchemy.orm import Session, sessionmaker

from app.live.protection import StateProtector
from app.live.repository import SqlAlchemyExecutionStoreRepository
from app.modules.rfq.store import ExecutionStore


class SqlAlchemyExecutionUnitOfWork:
    """Load, atomically checkpoint, and release one execution-store snapshot.

    Intended integration::

        with uow_factory() as uow:
            service = ProcurementExecutionService(store=uow.store, ...)
            result = await service.create_round(command)
            uow.commit()

    Exiting without ``commit`` rolls back.  This makes an exception raised by a
    command incapable of leaving a partial business-state checkpoint.
    """

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        snapshot_id: str = "default",
        state_protector: StateProtector | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._snapshot_id = snapshot_id
        self._state_protector = state_protector
        self.session: Session | None = None
        self.repository: SqlAlchemyExecutionStoreRepository | None = None
        self.store: ExecutionStore | None = None
        self._committed = False

    def __enter__(self) -> SqlAlchemyExecutionUnitOfWork:
        if self.session is not None:
            raise RuntimeError("unit of work cannot be entered more than once")
        self.session = self._session_factory()
        self.repository = SqlAlchemyExecutionStoreRepository(
            self.session,
            snapshot_id=self._snapshot_id,
            state_protector=self._state_protector,
        )
        self.repository.acquire_snapshot_lock()
        self.store = self.repository.load()
        return self

    def commit(self) -> None:
        if self.session is None or self.repository is None or self.store is None:
            raise RuntimeError("unit of work must be entered before commit")
        self.repository.save(self.store)
        self.session.commit()
        self._committed = True

    def rollback(self) -> None:
        if self.session is not None:
            self.session.rollback()

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_value, traceback
        if exc_type is not None or not self._committed:
            self.rollback()
        if self.session is not None:
            self.session.close()
        self.session = None
        self.repository = None
        self.store = None


class SqlAlchemyExecutionUnitOfWorkFactory:
    """Callable factory suitable for dependency injection in API handlers."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        snapshot_id: str = "default",
        state_protector: StateProtector | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._snapshot_id = snapshot_id
        self._state_protector = state_protector

    @property
    def state_protection_enabled(self) -> bool:
        return self._state_protector is not None

    def __call__(self) -> SqlAlchemyExecutionUnitOfWork:
        return SqlAlchemyExecutionUnitOfWork(
            self._session_factory,
            snapshot_id=self._snapshot_id,
            state_protector=self._state_protector,
        )


__all__ = [
    "SqlAlchemyExecutionUnitOfWork",
    "SqlAlchemyExecutionUnitOfWorkFactory",
]
