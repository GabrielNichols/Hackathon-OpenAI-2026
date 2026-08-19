"""Migration smoke and downgrade/upgrade contract against PostgreSQL."""

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

EXPECTED_TABLES = {
    "aggregate_records",
    "alembic_version",
    "audit_events",
    "consumed_link_nonces",
    "idempotency_records",
    "outbox_items",
}

pytestmark = pytest.mark.postgres


def _table_names(database_url: str) -> set[str]:
    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            return set(inspect(connection).get_table_names())
    finally:
        engine.dispose()


def test_migrations_upgrade_and_downgrade_clean_database(
    alembic_config: Config,
    migrated_database_url: str,
) -> None:
    assert _table_names(migrated_database_url) >= EXPECTED_TABLES

    command.downgrade(alembic_config, "base")
    assert not (EXPECTED_TABLES - {"alembic_version"}) & _table_names(migrated_database_url)

    command.upgrade(alembic_config, "head")
    assert _table_names(migrated_database_url) >= EXPECTED_TABLES
