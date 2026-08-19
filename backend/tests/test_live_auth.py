from __future__ import annotations

import base64

import pytest
from app.live.auth import (
    ConfiguredActorAuthenticator,
    LiveAuthenticationError,
    LiveRole,
)
from app.live.config import LiveSettings


def settings() -> LiveSettings:
    return LiveSettings(
        database_url="postgresql+psycopg://demo:secret@db.example.test/canal",
        public_base_url="https://canal.example.test",
        token_secret="t" * 32,
        csrf_secret="c" * 32,
        pii_hash_secret="p" * 32,
        operator_user_id="operator_lucas",
        operator_access_token="o" * 32,
        approver_user_id="buyer_gabriel",
        approver_access_token="a" * 32,
        tenant_id="org_demo",
    )


def test_authentication_resolves_identity_from_server_configuration():
    authenticator = ConfiguredActorAuthenticator(settings())

    actor = authenticator.authenticate(
        f"Bearer {'a' * 32}",
        required_role=LiveRole.APPROVER,
    )

    assert actor.user_id == "buyer_gabriel"
    assert actor.tenant_id == "org_demo"
    assert actor.role == LiveRole.APPROVER


def test_operator_token_cannot_approve_and_actor_id_cannot_be_declared():
    authenticator = ConfiguredActorAuthenticator(settings())

    with pytest.raises(LiveAuthenticationError, match="invalid"):
        authenticator.authenticate(
            f"Bearer {'o' * 32}",
            required_role=LiveRole.APPROVER,
        )
    with pytest.raises(LiveAuthenticationError, match="missing"):
        authenticator.authenticate(None, required_role=LiveRole.APPROVER)


def test_browser_basic_auth_is_bound_to_the_configured_identity():
    authenticator = ConfiguredActorAuthenticator(settings())
    credential = base64.b64encode(
        f"buyer_gabriel:{'a' * 32}".encode()
    ).decode()

    actor = authenticator.authenticate(
        f"Basic {credential}",
        required_role=LiveRole.APPROVER,
    )
    assert actor.user_id == "buyer_gabriel"

    forged_user = base64.b64encode(f"mallory:{'a' * 32}".encode()).decode()
    with pytest.raises(LiveAuthenticationError, match="invalid"):
        authenticator.authenticate(
            f"Basic {forged_user}",
            required_role=LiveRole.APPROVER,
        )
