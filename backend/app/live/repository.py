"""Durable repository for the Dev 4 execution-store state."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import fields
from typing import Protocol, runtime_checkable

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.live.codec import canonical_encoded_json, decode_state, encode_state
from app.live.models import (
    AuditEventRow,
    ExecutionSnapshotLockRow,
    ExecutionStateRow,
    IdempotencyRow,
)
from app.live.protection import StateProtector
from app.modules.rfq.contracts import AuditEventDTO
from app.modules.rfq.store import ExecutionStore

_SPECIAL_FIELDS = frozenset({"idempotency", "audit_events"})


class PersistenceIntegrityError(RuntimeError):
    """Persisted immutable data conflicts with the state being committed."""


@runtime_checkable
class ExecutionStoreRepository(Protocol):
    """Persistence boundary consumed by an execution unit of work."""

    def load(self) -> ExecutionStore: ...

    def save(self, store: ExecutionStore) -> None: ...

    def list_audit_events(
        self,
        *,
        tenant_id: str | None = None,
        aggregate_type: str | None = None,
        aggregate_id: str | None = None,
        correlation_id: str | None = None,
    ) -> list[AuditEventDTO]: ...


class SqlAlchemyExecutionStoreRepository:
    """Serialize the current store to relational rows using a safe JSON codec.

    Business state is checkpointed atomically by the surrounding transaction.
    Idempotency records and audit events are immutable and are therefore never
    deleted during a checkpoint.
    """

    def __init__(
        self,
        session: Session,
        *,
        snapshot_id: str = "default",
        state_protector: StateProtector | None = None,
    ) -> None:
        if not snapshot_id or len(snapshot_id) > 100:
            raise ValueError("snapshot_id must contain 1 to 100 characters")
        self.session = session
        self.snapshot_id = snapshot_id
        self._state_protector = state_protector
        self._snapshot_lock_acquired = False

    def acquire_snapshot_lock(self) -> None:
        """Serialize a snapshot transaction before any state is loaded.

        PostgreSQL uses ``SELECT ... FOR UPDATE`` on one control row per
        snapshot. Updating its revision also provides an equivalent early
        write lock for SQLite demo/test databases, where ``FOR UPDATE`` is a
        no-op. The lock is held until the injected Session commits or rolls
        back.
        """

        if self._snapshot_lock_acquired:
            return
        self._ensure_snapshot_lock_row()
        row = self.session.scalar(
            select(ExecutionSnapshotLockRow)
            .where(ExecutionSnapshotLockRow.snapshot_id == self.snapshot_id)
            .with_for_update()
        )
        if row is None:
            raise PersistenceIntegrityError(
                f"snapshot serialization row was not created: {self.snapshot_id!r}"
            )
        row.revision += 1
        self.session.flush()
        self._snapshot_lock_acquired = True

    def load(self) -> ExecutionStore:
        if not self._snapshot_lock_acquired:
            self.acquire_snapshot_lock()
        store = ExecutionStore()
        known_buckets = self._state_buckets()
        rows = self.session.scalars(
            select(ExecutionStateRow)
            .where(ExecutionStateRow.snapshot_id == self.snapshot_id)
            .order_by(ExecutionStateRow.bucket, ExecutionStateRow.key_hash)
        )
        for row in rows:
            if row.bucket not in known_buckets:
                raise PersistenceIntegrityError(
                    f"database contains an unknown execution-state bucket: {row.bucket!r}"
                )
            key_data = self._unprotect(row.key_data)
            expected_hash = self._encoded_key_hash(key_data)
            if expected_hash != row.key_hash:
                raise PersistenceIntegrityError(
                    f"execution-state key failed integrity check in bucket {row.bucket!r}"
                )
            bucket = getattr(store, row.bucket)
            key = decode_state(key_data)
            try:
                bucket[key] = decode_state(self._unprotect(row.value_data))
            except TypeError as error:
                raise PersistenceIntegrityError(
                    f"unhashable key found in execution-state bucket {row.bucket!r}"
                ) from error

        idempotency_rows = self.session.scalars(
            select(IdempotencyRow)
            .where(IdempotencyRow.snapshot_id == self.snapshot_id)
            .order_by(IdempotencyRow.operation, IdempotencyRow.idempotency_key)
        )
        for row in idempotency_rows:
            store.idempotency[(row.operation, row.idempotency_key)] = (
                row.fingerprint,
                decode_state(self._unprotect(row.result_data)),
            )

        store.audit_events = self.list_audit_events()
        return store

    def save(self, store: ExecutionStore) -> None:
        """Stage a full checkpoint in the caller's current SQL transaction."""

        if not self._snapshot_lock_acquired:
            self.acquire_snapshot_lock()
        self._replace_business_state(store)
        self._persist_idempotency(store)
        self._append_audit_events(store)
        self.session.flush()

    def list_audit_events(
        self,
        *,
        tenant_id: str | None = None,
        aggregate_type: str | None = None,
        aggregate_id: str | None = None,
        correlation_id: str | None = None,
    ) -> list[AuditEventDTO]:
        statement = select(AuditEventRow).where(AuditEventRow.snapshot_id == self.snapshot_id)
        if tenant_id is not None:
            statement = statement.where(AuditEventRow.tenant_id == tenant_id)
        if aggregate_type is not None:
            statement = statement.where(AuditEventRow.aggregate_type == aggregate_type)
        if aggregate_id is not None:
            statement = statement.where(AuditEventRow.aggregate_id == aggregate_id)
        if correlation_id is not None:
            statement = statement.where(AuditEventRow.correlation_id == correlation_id)
        rows = self.session.scalars(statement.order_by(AuditEventRow.position))
        events: list[AuditEventDTO] = []
        for row in rows:
            event = decode_state(self._unprotect(row.event_data))
            if not isinstance(event, AuditEventDTO):
                raise PersistenceIntegrityError(
                    f"audit row {row.event_id!r} did not decode to AuditEventDTO"
                )
            events.append(event)
        return events

    def _replace_business_state(self, store: ExecutionStore) -> None:
        self.session.execute(
            delete(ExecutionStateRow).where(ExecutionStateRow.snapshot_id == self.snapshot_id)
        )
        for bucket_name in sorted(self._state_buckets()):
            bucket = getattr(store, bucket_name)
            if not isinstance(bucket, Mapping):
                raise TypeError(f"execution-store field {bucket_name!r} must be a mapping")
            for key, value in bucket.items():
                key_data = encode_state(key)
                self.session.add(
                    ExecutionStateRow(
                        snapshot_id=self.snapshot_id,
                        bucket=bucket_name,
                        key_hash=self._encoded_key_hash(key_data),
                        key_data=self._protect(key_data),
                        value_data=self._protect(encode_state(value)),
                    )
                )

    def _persist_idempotency(self, store: ExecutionStore) -> None:
        for key, value in store.idempotency.items():
            if not isinstance(key, tuple) or len(key) != 2:
                raise TypeError("idempotency keys must be (operation, idempotency_key) tuples")
            if not isinstance(value, tuple) or len(value) != 2:
                raise TypeError("idempotency values must be (fingerprint, result) tuples")
            operation, idempotency_key = key
            fingerprint, result = value
            if not all(isinstance(item, str) and item for item in key):
                raise TypeError("idempotency operation and key must be non-empty strings")
            if not isinstance(fingerprint, str) or not fingerprint:
                raise TypeError("idempotency fingerprint must be a non-empty string")
            result_data = encode_state(result)
            existing = self.session.get(
                IdempotencyRow,
                {
                    "snapshot_id": self.snapshot_id,
                    "operation": operation,
                    "idempotency_key": idempotency_key,
                },
            )
            if existing is None:
                self.session.add(
                    IdempotencyRow(
                        snapshot_id=self.snapshot_id,
                        operation=operation,
                        idempotency_key=idempotency_key,
                        fingerprint=fingerprint,
                        result_data=self._protect(result_data),
                    )
                )
                continue
            if existing.fingerprint != fingerprint:
                raise PersistenceIntegrityError(
                    "idempotency key is already bound to a different request fingerprint: "
                    f"{operation}/{idempotency_key}"
                )
            if canonical_encoded_json(
                self._unprotect(existing.result_data)
            ) != canonical_encoded_json(result_data):
                raise PersistenceIntegrityError(
                    "idempotency key is already bound to a different result: "
                    f"{operation}/{idempotency_key}"
                )

    def _append_audit_events(self, store: ExecutionStore) -> None:
        seen_in_checkpoint: set[str] = set()
        for position, event in enumerate(store.audit_events):
            if not isinstance(event, AuditEventDTO):
                raise TypeError("audit_events may only contain AuditEventDTO values")
            if event.event_id in seen_in_checkpoint:
                raise PersistenceIntegrityError(
                    f"duplicate audit event id in checkpoint: {event.event_id!r}"
                )
            seen_in_checkpoint.add(event.event_id)
            event_data = encode_state(event)
            existing = self.session.get(
                AuditEventRow,
                {"snapshot_id": self.snapshot_id, "event_id": event.event_id},
            )
            if existing is not None:
                persisted_event = canonical_encoded_json(self._unprotect(existing.event_data))
                checkpoint_event = canonical_encoded_json(event_data)
                if persisted_event != checkpoint_event:
                    raise PersistenceIntegrityError(
                        f"audit event is immutable but payload changed: {event.event_id!r}"
                    )
                continue
            self.session.add(
                AuditEventRow(
                    snapshot_id=self.snapshot_id,
                    event_id=event.event_id,
                    position=position,
                    tenant_id=event.tenant_id,
                    event_type=event.event_type,
                    aggregate_type=event.aggregate_type,
                    aggregate_id=event.aggregate_id,
                    occurred_at=event.occurred_at,
                    correlation_id=event.correlation_id,
                    event_data=self._protect(event_data),
                )
            )

    @staticmethod
    def _state_buckets() -> frozenset[str]:
        return frozenset(
            field.name for field in fields(ExecutionStore) if field.name not in _SPECIAL_FIELDS
        )

    @staticmethod
    def _encoded_key_hash(key_data: object) -> str:
        encoded = canonical_encoded_json(key_data).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def _ensure_snapshot_lock_row(self) -> None:
        values = {"snapshot_id": self.snapshot_id, "revision": 0}
        dialect_name = self.session.get_bind().dialect.name
        if dialect_name == "postgresql":
            from sqlalchemy.dialects.postgresql import insert

            statement = insert(ExecutionSnapshotLockRow).values(**values)
            self.session.execute(statement.on_conflict_do_nothing(index_elements=["snapshot_id"]))
            return
        if dialect_name == "sqlite":
            from sqlalchemy.dialects.sqlite import insert

            statement = insert(ExecutionSnapshotLockRow).values(**values)
            self.session.execute(statement.on_conflict_do_nothing(index_elements=["snapshot_id"]))
            return
        if self.session.get(ExecutionSnapshotLockRow, self.snapshot_id) is not None:
            return
        try:
            with self.session.begin_nested():
                self.session.add(ExecutionSnapshotLockRow(**values))
                self.session.flush()
        except IntegrityError:
            # Another transaction created the same control row; locking it in
            # the next statement is the only state transition we need.
            pass

    def _protect(self, encoded_value: object) -> object:
        if self._state_protector is None:
            return encoded_value
        return self._state_protector.protect(encoded_value)

    def _unprotect(self, persisted_value: object) -> object:
        if self._state_protector is None:
            return persisted_value
        return self._state_protector.unprotect(persisted_value)


__all__ = [
    "ExecutionStoreRepository",
    "PersistenceIntegrityError",
    "SqlAlchemyExecutionStoreRepository",
]
