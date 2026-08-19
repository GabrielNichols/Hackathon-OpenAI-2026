"""Fail-closed configuration for the real demo runtime."""

from __future__ import annotations

import os
from dataclasses import dataclass
from urllib.parse import urlparse


class LiveConfigurationError(RuntimeError):
    """The process cannot safely start in live mode."""


@dataclass(frozen=True, slots=True)
class LiveSettings:
    database_url: str
    public_base_url: str
    token_secret: str
    csrf_secret: str
    pii_hash_secret: str
    operator_user_id: str
    operator_access_token: str
    approver_user_id: str
    approver_access_token: str
    tenant_id: str
    allow_insecure_http: bool = False
    allow_test_database: bool = False

    def __post_init__(self) -> None:
        _require_non_empty(self.database_url, "database_url")
        _require_non_empty(self.public_base_url, "public_base_url")
        _require_non_empty(self.token_secret, "token_secret")
        _require_non_empty(self.csrf_secret, "csrf_secret")
        _require_non_empty(self.pii_hash_secret, "pii_hash_secret")
        _require_non_empty(self.operator_user_id, "operator_user_id")
        _require_non_empty(self.operator_access_token, "operator_access_token")
        _require_non_empty(self.approver_user_id, "approver_user_id")
        _require_non_empty(self.approver_access_token, "approver_access_token")
        _require_non_empty(self.tenant_id, "tenant_id")

        if len(self.token_secret.encode("utf-8")) < 32:
            raise LiveConfigurationError("token_secret must contain at least 32 bytes")
        if len(self.csrf_secret.encode("utf-8")) < 32:
            raise LiveConfigurationError("csrf_secret must contain at least 32 bytes")
        if len(self.pii_hash_secret.encode("utf-8")) < 32:
            raise LiveConfigurationError(
                "pii_hash_secret must contain at least 32 bytes"
            )
        if len({self.token_secret, self.csrf_secret, self.pii_hash_secret}) != 3:
            raise LiveConfigurationError(
                "token, CSRF and PII hashing secrets must be different"
            )
        if len(self.operator_access_token) < 24:
            raise LiveConfigurationError(
                "operator_access_token must contain at least 24 characters"
            )
        if len(self.approver_access_token) < 24:
            raise LiveConfigurationError(
                "approver_access_token must contain at least 24 characters"
            )
        if self.operator_access_token == self.approver_access_token:
            raise LiveConfigurationError(
                "operator and approver must use different credentials"
            )

        parsed_database = urlparse(self.database_url)
        supported_schemes = {
            "postgres",
            "postgresql",
            "postgresql+psycopg",
        }
        is_explicit_test_database = (
            self.allow_test_database and parsed_database.scheme == "sqlite"
        )
        if is_explicit_test_database and parsed_database.path in {"", "/:memory:"}:
            raise LiveConfigurationError("test database must be a durable SQLite file")
        if parsed_database.scheme not in supported_schemes and not is_explicit_test_database:
            raise LiveConfigurationError(
                "live database_url must use PostgreSQL, never an in-memory database"
            )

        parsed_public_url = urlparse(self.public_base_url)
        if (
            parsed_public_url.username
            or parsed_public_url.password
            or parsed_public_url.query
            or parsed_public_url.fragment
        ):
            raise LiveConfigurationError(
                "public_base_url must not contain credentials, query or fragment"
            )
        if not parsed_public_url.netloc:
            raise LiveConfigurationError("public_base_url must be absolute")
        if parsed_public_url.path not in {"", "/"}:
            raise LiveConfigurationError("public_base_url must be an origin without a path")
        if not self.allow_insecure_http and parsed_public_url.scheme != "https":
            raise LiveConfigurationError("live public_base_url must use HTTPS")
        if self.allow_insecure_http and parsed_public_url.scheme not in {"http", "https"}:
            raise LiveConfigurationError("public_base_url must use HTTP or HTTPS")

        object.__setattr__(self, "public_base_url", self.public_base_url.rstrip("/"))

    @classmethod
    def from_env(cls) -> LiveSettings:
        mode = os.environ.get("CANAL_AGENT_MODE", "").strip().lower()
        if mode != "live":
            raise LiveConfigurationError(
                "CANAL_AGENT_MODE must be explicitly set to 'live'"
            )
        return cls(
            database_url=_required_env("CANAL_AGENT_DATABASE_URL"),
            public_base_url=_required_env("CANAL_AGENT_PUBLIC_BASE_URL"),
            token_secret=_required_env("CANAL_AGENT_TOKEN_SECRET"),
            csrf_secret=_required_env("CANAL_AGENT_CSRF_SECRET"),
            pii_hash_secret=_required_env("CANAL_AGENT_PII_HASH_SECRET"),
            operator_user_id=_required_env("CANAL_AGENT_OPERATOR_USER_ID"),
            operator_access_token=_required_env(
                "CANAL_AGENT_OPERATOR_ACCESS_TOKEN"
            ),
            approver_user_id=_required_env("CANAL_AGENT_APPROVER_USER_ID"),
            approver_access_token=_required_env(
                "CANAL_AGENT_APPROVER_ACCESS_TOKEN"
            ),
            tenant_id=_required_env("CANAL_AGENT_TENANT_ID"),
        )


def reject_fake_live_component(component: object, *, role: str) -> None:
    """Fail closed if a test-only component reaches the live dependency graph."""

    component_type = type(component)
    qualified_name = f"{component_type.__module__}.{component_type.__name__}"
    forbidden_names = {
        "app.modules.messaging.gateway.FakeDeliveryGateway",
        "app.modules.rfq.store.InMemoryExecutionStore",
    }
    if qualified_name in forbidden_names or component_type.__name__.lower().startswith(
        "fake"
    ):
        raise LiveConfigurationError(
            f"live {role} cannot use test component {qualified_name}"
        )


def _required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise LiveConfigurationError(f"missing required environment variable: {name}")
    return value


def _require_non_empty(value: str, field: str) -> None:
    if not value.strip():
        raise LiveConfigurationError(f"{field} must not be empty")


__all__ = [
    "LiveConfigurationError",
    "LiveSettings",
    "reject_fake_live_component",
]
