from __future__ import annotations

import json

import pytest
from app.live.database import (
    create_database_engine,
    create_schema,
    create_session_factory,
)
from app.live.protection import AesGcmStateProtector, StateProtectionError
from app.live.uow import SqlAlchemyExecutionUnitOfWorkFactory


def test_authenticated_state_protection_round_trip_and_tamper_rejection() -> None:
    protector = AesGcmStateProtector("state-secret-with-at-least-thirty-two-bytes")
    original = {"token": "private-token", "nested": [1, True, None]}

    envelope = protector.protect(original)

    assert protector.unprotect(envelope) == original
    assert "private-token" not in json.dumps(envelope)
    encoded = next(iter(envelope.values()))
    replacement = "A" if encoded[-2] != "A" else "B"
    tampered = {next(iter(envelope)): f"{encoded[:-2]}{replacement}{encoded[-1]}"}
    with pytest.raises(StateProtectionError):
        protector.unprotect(tampered)


def test_live_uow_encrypts_tokens_and_contacts_and_rehydrates_after_restart(
    tmp_path,
) -> None:
    database_path = tmp_path / "protected-state.db"
    engine = create_database_engine(f"sqlite:///{database_path}")
    create_schema(engine)
    sessions = create_session_factory(engine)
    protector = AesGcmStateProtector("state-secret-with-at-least-thirty-two-bytes")
    factory = SqlAlchemyExecutionUnitOfWorkFactory(
        sessions,
        snapshot_id="tenant-protected",
        state_protector=protector,
    )
    raw_token = "signed-internal-token-that-must-not-be-plain"
    raw_contact = "private-contact@example.com"

    with factory() as uow:
        assert uow.store is not None
        uow.store.recipients["recipient_1"] = {"response_token": raw_token}
        uow.store.quotes["quote_1"] = {"respondent_contact": raw_contact}
        uow.store.idempotency[("tenant:operation", "key")] = (
            "fingerprint",
            {"contact": raw_contact},
        )
        uow.commit()
    engine.dispose()

    database_bytes = database_path.read_bytes()
    assert raw_token.encode() not in database_bytes
    assert raw_contact.encode() not in database_bytes

    restarted = create_database_engine(f"sqlite:///{database_path}")
    restarted_factory = SqlAlchemyExecutionUnitOfWorkFactory(
        create_session_factory(restarted),
        snapshot_id="tenant-protected",
        state_protector=protector,
    )
    with restarted_factory() as uow:
        assert uow.store is not None
        assert uow.store.recipients["recipient_1"]["response_token"] == raw_token
        assert uow.store.quotes["quote_1"]["respondent_contact"] == raw_contact
    restarted.dispose()
