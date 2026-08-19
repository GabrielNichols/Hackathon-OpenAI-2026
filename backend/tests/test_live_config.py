from __future__ import annotations

import pytest

from app.live.config import (
    LiveConfigurationError,
    LiveSettings,
    reject_fake_live_component,
)
from app.modules.messaging.gateway import FakeDeliveryGateway


def valid_settings(**updates: object) -> LiveSettings:
    values: dict[str, object] = {
        "database_url": "postgresql+psycopg://demo:secret@db.example.test/canal",
        "public_base_url": "https://canal.example.test/",
        "token_secret": "t" * 32,
        "csrf_secret": "c" * 32,
        "pii_hash_secret": "p" * 32,
        "operator_user_id": "operator_lucas",
        "operator_access_token": "o" * 32,
        "approver_user_id": "buyer_gabriel",
        "approver_access_token": "a" * 32,
        "tenant_id": "org_demo",
    }
    values.update(updates)
    return LiveSettings(**values)  # type: ignore[arg-type]


def test_live_settings_require_postgres_https_and_distinct_credentials():
    settings = valid_settings()

    assert settings.public_base_url == "https://canal.example.test"

    with pytest.raises(LiveConfigurationError, match="PostgreSQL"):
        valid_settings(database_url="sqlite:///demo.db")
    with pytest.raises(LiveConfigurationError, match="HTTPS"):
        valid_settings(public_base_url="http://canal.example.test")
    with pytest.raises(LiveConfigurationError, match="different credentials"):
        valid_settings(approver_access_token="o" * 32)


def test_live_settings_fail_closed_when_mode_or_secret_is_missing(monkeypatch):
    monkeypatch.delenv("CANAL_AGENT_MODE", raising=False)

    with pytest.raises(LiveConfigurationError, match="explicitly"):
        LiveSettings.from_env()


def test_live_dependency_graph_rejects_fake_gateway():
    with pytest.raises(LiveConfigurationError, match="FakeDeliveryGateway"):
        reject_fake_live_component(FakeDeliveryGateway(), role="delivery gateway")


def test_sqlite_is_available_only_as_an_explicit_file_backed_test_database(tmp_path):
    settings = valid_settings(
        database_url=f"sqlite:///{tmp_path / 'live-test.db'}",
        allow_test_database=True,
    )
    assert settings.allow_test_database is True

    with pytest.raises(LiveConfigurationError, match="durable SQLite file"):
        valid_settings(database_url="sqlite:///:memory:", allow_test_database=True)
