"""Real PostgreSQL fixtures; importing this module never starts Docker."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from testcontainers.community.postgres import PostgresContainer

from app.infrastructure.db.session import create_async_engine, create_session_factory

BACKEND_ROOT = Path(__file__).resolve().parents[3]
TABLES = (
    "audit_events",
    "consumed_link_nonces",
    "idempotency_records",
    "outbox_items",
    "aggregate_records",
)


def _psycopg_url(url: str) -> str:
    if url.startswith("postgresql+psycopg2://"):
        return url.replace("postgresql+psycopg2://", "postgresql+psycopg://", 1)
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+psycopg://", 1)
    return url


@pytest.fixture(scope="session")
def postgres_url() -> Iterator[str]:
    configured_url = os.getenv("CORE_TEST_DATABASE_URL")
    if configured_url:
        yield _psycopg_url(configured_url)
        return

    try:
        with PostgresContainer("postgres:16-alpine") as postgres:
            yield _psycopg_url(postgres.get_connection_url())
    except Exception as exc:
        pytest.fail(
            "PostgreSQL integration tests require CORE_TEST_DATABASE_URL or a working "
            f"Docker daemon: {exc}",
            pytrace=False,
        )


@pytest.fixture(scope="session")
def alembic_config(postgres_url: str) -> Config:
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", postgres_url)
    return config


@pytest.fixture(scope="session")
def migrated_database_url(alembic_config: Config, postgres_url: str) -> str:
    command.upgrade(alembic_config, "head")
    return postgres_url


@pytest_asyncio.fixture(scope="session")
async def db_engine(migrated_database_url: str) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(migrated_database_url)
    yield engine
    await engine.dispose()


@pytest.fixture(scope="session")
def session_factory(
    db_engine: AsyncEngine,
) -> async_sessionmaker[AsyncSession]:
    return create_session_factory(db_engine)


@pytest_asyncio.fixture
async def clean_database(db_engine: AsyncEngine) -> AsyncIterator[None]:
    table_list = ", ".join(TABLES)
    async with db_engine.begin() as connection:
        await connection.execute(text(f"TRUNCATE TABLE {table_list} RESTART IDENTITY CASCADE"))
    yield
    async with db_engine.begin() as connection:
        await connection.execute(text(f"TRUNCATE TABLE {table_list} RESTART IDENTITY CASCADE"))
