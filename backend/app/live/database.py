"""Database wiring shared by the live repository and application lifespan."""

from __future__ import annotations

from collections.abc import Callable

from sqlalchemy import Engine, create_engine
from sqlalchemy.engine import URL, make_url
from sqlalchemy.orm import Session, sessionmaker

from app.live.models import Base

SessionFactory = Callable[[], Session]


def normalize_database_url(database_url: str) -> URL:
    """Normalize common platform PostgreSQL URLs to the psycopg 3 dialect."""

    if not database_url or not database_url.strip():
        raise ValueError("DATABASE_URL must not be empty")
    normalized = database_url.strip()
    if normalized.startswith("postgres://"):
        normalized = "postgresql://" + normalized.removeprefix("postgres://")
    url = make_url(normalized)
    if url.drivername == "postgresql":
        url = url.set(drivername="postgresql+psycopg")
    return url


def create_database_engine(database_url: str, *, echo: bool = False) -> Engine:
    """Create a production-ready engine; the caller owns disposal."""

    url = normalize_database_url(database_url)
    connect_args: dict[str, object] = {}
    if url.drivername.startswith("sqlite"):
        # FastAPI may execute request handlers on worker threads during the demo.
        connect_args["check_same_thread"] = False
    return create_engine(
        url,
        echo=echo,
        future=True,
        pool_pre_ping=True,
        connect_args=connect_args,
    )


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)


def create_schema(engine: Engine) -> None:
    """Create the minimal demo schema.

    A production rollout can replace this call with Alembic migrations without
    changing the repository contract.
    """

    Base.metadata.create_all(engine)


__all__ = [
    "SessionFactory",
    "create_database_engine",
    "create_schema",
    "create_session_factory",
    "normalize_database_url",
]
