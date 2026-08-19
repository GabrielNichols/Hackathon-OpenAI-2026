from __future__ import annotations

import time as time_module
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, time
from threading import Barrier

import pytest
from app.live.codec import KNOWN_DTOS, StateCodecError, decode_state
from app.live.database import (
    create_database_engine,
    create_schema,
    create_session_factory,
    normalize_database_url,
)
from app.live.models import ExecutionSnapshotLockRow
from app.live.repository import (
    PersistenceIntegrityError,
    SqlAlchemyExecutionStoreRepository,
)
from app.live.uow import SqlAlchemyExecutionUnitOfWorkFactory
from app.modules.rfq.contracts import (
    ActorType,
    AuditEventDTO,
    ExecutionPolicySnapshotDTO,
    RFQRequirementsSnapshotDTO,
    RFQRoundDTO,
    RFQRoundStatus,
)

NOW = datetime(2026, 8, 19, 15, 30, tzinfo=UTC)


def _round() -> RFQRoundDTO:
    return RFQRoundDTO(
        rfq_round_id="rfq_0001",
        procurement_request_id="request_0001",
        request_version=1,
        round_version=2,
        status=RFQRoundStatus.ACTIVE,
        recipient_count=2,
        response_deadline=datetime(2026, 8, 20, 18, tzinfo=UTC),
        requirements_snapshot_hash="requirements-hash",
        policy_snapshot_hash="policy-hash",
        created_at=NOW,
    )


def _requirements() -> RFQRequirementsSnapshotDTO:
    return RFQRequirementsSnapshotDTO(
        description="Almoco corporativo",
        category="alimentacao",
        event_date=date(2026, 8, 28),
        delivery_time=time(12, 30),
        timezone="America/Sao_Paulo",
        location_city="Sao Paulo",
        people_count=30,
        maximum_total_cents=150_000,
        vegetarian_count=3,
        invoice_required=True,
        mandatory_requirements=["nota fiscal"],
    )


def _policy() -> ExecutionPolicySnapshotDTO:
    return ExecutionPolicySnapshotDTO(
        source_policy_version=1,
        minimum_confirmed_deliveries=2,
        maximum_total_cents=150_000,
        target_total_cents=130_000,
        ranking_weights={"price": 70, "sustainability": 30},
        approver_user_id="buyer_001",
    )


def _audit(event_id: str, event_type: str) -> AuditEventDTO:
    return AuditEventDTO(
        event_id=event_id,
        tenant_id="tenant_demo",
        event_type=event_type,
        aggregate_type="rfq_round",
        aggregate_id="rfq_0001",
        occurred_at=NOW,
        correlation_id="cor_request_0001",
        actor_type=ActorType.SYSTEM,
        actor_id="dev4_execution_service",
        payload={"round_version": 2},
    )


def _factory(database_url: str) -> tuple[object, SqlAlchemyExecutionUnitOfWorkFactory]:
    engine = create_database_engine(database_url)
    create_schema(engine)
    sessions = create_session_factory(engine)
    return engine, SqlAlchemyExecutionUnitOfWorkFactory(sessions, snapshot_id="demo")


def test_store_idempotency_audit_and_counters_survive_process_restart(tmp_path) -> None:
    database_url = f"sqlite:///{tmp_path / 'live.db'}"
    engine, factory = _factory(database_url)

    with factory() as uow:
        assert uow.store is not None
        uow.store.rounds["rfq_0001"] = {
            "dto": _round(),
            "tenant_id": "tenant_demo",
            "requirements": _requirements(),
            "policy": _policy(),
            "recipient_ids": ["recipient_0001", "recipient_0002"],
        }
        uow.store.recipients["recipient_0001"] = {"status": "DELIVERED"}
        uow.store.quote_versions[("quote_0001", 1)] = {"total_cents": 123_450}
        uow.store.procurement_status["request_0001"] = "RFQ_ACTIVE"
        uow.store.id_counters.update({"rfq": 1, "event": 1, "quote": 1})
        uow.store.idempotency[("rfq.create", "idem-create-1")] = (
            "fingerprint-1",
            _round(),
        )
        uow.store.audit_events.append(_audit("event_0001", "RFQ_ROUND_ACTIVATED"))
        uow.commit()

    engine.dispose()

    restarted_engine, restarted_factory = _factory(database_url)
    with restarted_factory() as uow:
        assert uow.store is not None
        round_record = uow.store.rounds["rfq_0001"]
        assert isinstance(round_record["dto"], RFQRoundDTO)
        assert isinstance(round_record["requirements"], RFQRequirementsSnapshotDTO)
        assert isinstance(round_record["policy"], ExecutionPolicySnapshotDTO)
        assert round_record["requirements"].event_date == date(2026, 8, 28)
        assert uow.store.quote_versions[("quote_0001", 1)]["total_cents"] == 123_450
        assert uow.store.procurement_status["request_0001"] == "RFQ_ACTIVE"
        assert uow.store.id_counters == {"rfq": 1, "event": 1, "quote": 1}
        fingerprint, replay = uow.store.idempotency[("rfq.create", "idem-create-1")]
        assert fingerprint == "fingerprint-1"
        assert isinstance(replay, RFQRoundDTO)
        assert [event.event_id for event in uow.store.audit_events] == ["event_0001"]

        # A restarted service can continue IDs without colliding with prior rows.
        uow.store.id_counters["event"] += 1
        uow.store.audit_events.append(_audit("event_0002", "RFQ_STATUS_READ"))
        uow.commit()

    restarted_engine.dispose()

    final_engine, final_factory = _factory(database_url)
    with final_factory() as uow:
        assert uow.store is not None
        assert uow.store.id_counters["event"] == 2
        assert [event.event_id for event in uow.store.audit_events] == [
            "event_0001",
            "event_0002",
        ]
        assert uow.repository is not None
        filtered = uow.repository.list_audit_events(
            tenant_id="tenant_demo",
            aggregate_type="rfq_round",
            aggregate_id="rfq_0001",
            correlation_id="cor_request_0001",
        )
        assert [event.event_id for event in filtered] == ["event_0001", "event_0002"]
    final_engine.dispose()


def test_exit_without_commit_rolls_back_checkpoint(tmp_path) -> None:
    database_url = f"sqlite:///{tmp_path / 'rollback.db'}"
    engine, factory = _factory(database_url)

    with factory() as uow:
        assert uow.store is not None
        uow.store.procurement_status["request_rolled_back"] = "SHOULD_NOT_EXIST"

    with factory() as uow:
        assert uow.store is not None
        assert "request_rolled_back" not in uow.store.procurement_status
    engine.dispose()


def test_idempotency_binding_is_immutable(tmp_path) -> None:
    database_url = f"sqlite:///{tmp_path / 'idempotency.db'}"
    engine, factory = _factory(database_url)

    with factory() as uow:
        assert uow.store is not None
        uow.store.idempotency[("rfq.create", "same-key")] = ("hash-a", _round())
        uow.commit()

    with (
        pytest.raises(PersistenceIntegrityError, match="different request fingerprint"),
        factory() as uow,
    ):
        assert uow.store is not None
        uow.store.idempotency[("rfq.create", "same-key")] = ("hash-b", _round())
        uow.commit()

    with factory() as uow:
        assert uow.store is not None
        fingerprint, _ = uow.store.idempotency[("rfq.create", "same-key")]
        assert fingerprint == "hash-a"
    engine.dispose()


def test_codec_rejects_unknown_dto_instead_of_importing_it() -> None:
    assert "RFQRoundDTO" in KNOWN_DTOS
    with pytest.raises(StateCodecError, match="unknown contract DTO"):
        decode_state(
            {
                "__canal_agente_kind__": "dto",
                "name": "os.system",
                "data": {"__canal_agente_kind__": "mapping", "items": []},
            }
        )


def test_postgres_database_url_uses_psycopg3_driver() -> None:
    assert (
        normalize_database_url("postgres://user:secret@db.example/canal").drivername
        == "postgresql+psycopg"
    )
    assert (
        normalize_database_url("postgresql://user:secret@db.example/canal").drivername
        == "postgresql+psycopg"
    )


def test_uow_acquires_snapshot_lock_before_loading_state(tmp_path, monkeypatch) -> None:
    database_url = f"sqlite:///{tmp_path / 'snapshot-order.db'}"
    engine, factory = _factory(database_url)
    calls: list[str] = []
    original_acquire = SqlAlchemyExecutionStoreRepository.acquire_snapshot_lock
    original_load = SqlAlchemyExecutionStoreRepository.load

    def observed_acquire(repository) -> None:
        calls.append("lock")
        original_acquire(repository)

    def observed_load(repository):
        calls.append("load")
        return original_load(repository)

    monkeypatch.setattr(
        SqlAlchemyExecutionStoreRepository,
        "acquire_snapshot_lock",
        observed_acquire,
    )
    monkeypatch.setattr(SqlAlchemyExecutionStoreRepository, "load", observed_load)

    with factory() as uow:
        assert calls[:2] == ["lock", "load"]
        assert uow.session is not None
        lock = uow.session.get(ExecutionSnapshotLockRow, "demo")
        assert lock is not None
        assert lock.revision == 1
        uow.commit()
    engine.dispose()


def test_snapshot_lock_prevents_lost_update_between_concurrent_uows(tmp_path) -> None:
    database_url = f"sqlite:///{tmp_path / 'snapshot-concurrency.db'}"
    engine, factory = _factory(database_url)
    with factory() as uow:
        assert uow.store is not None
        uow.store.id_counters["quote"] = 0
        uow.commit()

    start = Barrier(2)

    def increment() -> None:
        start.wait(timeout=5)
        with factory() as uow:
            assert uow.store is not None
            current = uow.store.id_counters["quote"]
            # Make both workers overlap; the second must still load only after
            # the first transaction releases the snapshot control row.
            time_module.sleep(0.05)
            uow.store.id_counters["quote"] = current + 1
            uow.commit()

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(increment) for _ in range(2)]
        for future in futures:
            future.result(timeout=10)

    with factory() as uow:
        assert uow.store is not None
        assert uow.store.id_counters["quote"] == 2
    engine.dispose()
