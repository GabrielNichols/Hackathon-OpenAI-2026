"""SQLAlchemy persistence for real manual-link delivery evidence."""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any
from urllib.parse import parse_qs, urlsplit

from sqlalchemy import exists, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.live.codec import decode_state, encode_state
from app.live.models import ManualDeliveryActivityRow, ManualLinkDeliveryRow
from app.live.repository import PersistenceIntegrityError
from app.modules.messaging.gateway import (
    DeliveryState,
    GatewayError,
    GatewayIdempotencyConflict,
    GatewayMessageNotFound,
)
from app.modules.messaging.manual_link import (
    ManualDeliveryAction,
    ManualDeliveryActivity,
    ManualLinkDeliveryRecord,
)

_SENSITIVE_METADATA_KEYS = frozenset(
    {
        "access_token",
        "client_ip",
        "contact",
        "email",
        "password",
        "phone",
        "recipient_contact",
        "response_token",
        "secret",
        "token",
        "user_agent",
    }
)
_FORBIDDEN_LINK_QUERY_KEYS = frozenset({"access_token", "response_token", "secret", "token"})


class SqlAlchemyManualLinkDeliveryRepository:
    """Durable implementation of ``ManualLinkDeliveryRepository``.

    The injected ``Session`` determines the transaction boundary, allowing a
    delivery update and the procurement checkpoint to commit together.  The
    repository flushes constraints but never commits or closes that session.
    """

    def __init__(self, session: Session, *, pii_hash_secret: str | bytes) -> None:
        secret = (
            pii_hash_secret.encode("utf-8") if isinstance(pii_hash_secret, str) else pii_hash_secret
        )
        if len(secret) < 32:
            raise ValueError("pii_hash_secret must contain at least 32 bytes")
        self.session = session
        self._pii_hash_key = hmac.new(
            secret,
            b"canal-agente/manual-link/pii/v1",
            hashlib.sha256,
        ).digest()

    async def get_by_external_id(self, external_id: str) -> ManualLinkDeliveryRecord | None:
        row = self.session.get(ManualLinkDeliveryRow, external_id)
        return self._to_record(row) if row is not None else None

    async def get_by_idempotency_key(self, idempotency_key: str) -> ManualLinkDeliveryRecord | None:
        row = self.session.scalar(
            select(ManualLinkDeliveryRow).where(
                ManualLinkDeliveryRow.idempotency_key == idempotency_key
            )
        )
        return self._to_record(row) if row is not None else None

    async def get_by_response_token_digest(
        self, response_token_digest: str
    ) -> ManualLinkDeliveryRecord | None:
        row = self.session.scalar(
            select(ManualLinkDeliveryRow).where(
                ManualLinkDeliveryRow.response_token_digest == response_token_digest
            )
        )
        return self._to_record(row) if row is not None else None

    async def list_records(self) -> Sequence[ManualLinkDeliveryRecord]:
        rows = self.session.scalars(
            select(ManualLinkDeliveryRow).order_by(
                ManualLinkDeliveryRow.accepted_at,
                ManualLinkDeliveryRow.external_id,
            )
        )
        return tuple(self._to_record(row) for row in rows)

    async def create(
        self,
        record: ManualLinkDeliveryRecord,
        activity: ManualDeliveryActivity,
    ) -> None:
        """Atomically stage the delivery and its mandatory LINK_CREATED event."""

        self._validate_created_pair(record, activity)
        try:
            # A savepoint maps uniqueness races without poisoning the caller's
            # wider procurement transaction.
            with self.session.begin_nested():
                self.session.add(self._delivery_row(record))
                self.session.flush()
                self.session.add(self._activity_row(activity))
                self.session.flush()
        except IntegrityError as error:
            raise GatewayIdempotencyConflict(
                "manual delivery external_id, idempotency key, token digest, "
                "or creation activity already exists"
            ) from error

    async def append_activity(self, activity: ManualDeliveryActivity) -> None:
        if self.session.get(ManualLinkDeliveryRow, activity.external_id) is None:
            raise GatewayMessageNotFound(f"Unknown gateway external_id: {activity.external_id}")
        if activity.action in {
            ManualDeliveryAction.LINK_CREATED,
            ManualDeliveryAction.SUPPLIER_OPENED,
        }:
            raise GatewayError(
                f"{activity.action} must be written by its atomic repository operation"
            )
        try:
            with self.session.begin_nested():
                self.session.add(self._activity_row(activity))
                self.session.flush()
        except IntegrityError as error:
            raise PersistenceIntegrityError(
                f"manual delivery activity id already exists: {activity.activity_id!r}"
            ) from error

    async def has_activity(
        self,
        external_id: str,
        action: ManualDeliveryAction,
    ) -> bool:
        activity_id = self.session.scalar(
            select(ManualDeliveryActivityRow.activity_id)
            .where(
                ManualDeliveryActivityRow.external_id == external_id,
                ManualDeliveryActivityRow.action == action.value,
            )
            .limit(1)
        )
        return activity_id is not None

    async def list_activities(self, external_id: str) -> Sequence[ManualDeliveryActivity]:
        rows = self.session.scalars(
            select(ManualDeliveryActivityRow)
            .where(ManualDeliveryActivityRow.external_id == external_id)
            .order_by(ManualDeliveryActivityRow.sequence_id)
        )
        return tuple(self._to_activity(row) for row in rows)

    async def mark_delivered_on_open(
        self,
        external_id: str,
        delivered_at: datetime,
        activity: ManualDeliveryActivity,
    ) -> ManualLinkDeliveryRecord:
        """Conditionally transition once and append opening evidence atomically.

        The conditional SQL update is safe under concurrent PostgreSQL
        requests: after the winner commits, another updater re-evaluates the
        status predicate and affects zero rows.  A unique deduplication key on
        the opening activity provides a second database-level guard.
        """

        delivered_at = _as_utc(delivered_at)
        self._validate_open_activity(external_id, activity)
        won_transition = False
        try:
            with self.session.begin_nested():
                send_was_recorded = exists(
                    select(ManualDeliveryActivityRow.sequence_id).where(
                        ManualDeliveryActivityRow.external_id == external_id,
                        ManualDeliveryActivityRow.action
                        == ManualDeliveryAction.SEND_RECORDED.value,
                    )
                )
                result = self.session.execute(
                    update(ManualLinkDeliveryRow)
                    .where(
                        ManualLinkDeliveryRow.external_id == external_id,
                        ManualLinkDeliveryRow.status != DeliveryState.DELIVERED.value,
                        send_was_recorded,
                    )
                    .values(
                        status=DeliveryState.DELIVERED.value,
                        delivered_at=delivered_at,
                        version=ManualLinkDeliveryRow.version + 1,
                    )
                    .execution_options(synchronize_session=False)
                )
                won_transition = result.rowcount == 1
                if won_transition:
                    self.session.add(self._activity_row(activity))
                    self.session.flush()
        except IntegrityError:
            # A concurrent transaction may have inserted the uniquely keyed
            # SUPPLIER_OPENED activity. The savepoint also reverts our update.
            won_transition = False

        row = self.session.scalar(
            select(ManualLinkDeliveryRow)
            .where(ManualLinkDeliveryRow.external_id == external_id)
            .execution_options(populate_existing=True)
        )
        if row is None:
            raise GatewayMessageNotFound(f"Unknown gateway external_id: {external_id}")
        if row.status != DeliveryState.DELIVERED.value:
            if not await self.has_activity(external_id, ManualDeliveryAction.SEND_RECORDED):
                raise GatewayError(
                    "Supplier delivery cannot be confirmed before the manual send is recorded"
                )
            if won_transition:
                raise PersistenceIntegrityError(
                    "manual delivery transition was not visible after flush"
                )
            raise PersistenceIntegrityError(
                "concurrent manual delivery transition did not produce DELIVERED state"
            )
        return self._to_record(row)

    def _delivery_row(self, record: ManualLinkDeliveryRecord) -> ManualLinkDeliveryRow:
        _validate_safe_public_link(record.public_link, record.external_id)
        _validate_sha256_digest(record.response_token_digest, "response_token_digest")
        _validate_sha256_digest(record.fingerprint, "fingerprint")
        if record.status != DeliveryState.SENT_TO_GATEWAY or record.delivered_at is not None:
            raise ValueError("a new manual delivery must be pending and not delivered")
        return ManualLinkDeliveryRow(
            external_id=record.external_id,
            idempotency_key=record.idempotency_key,
            recipient_id=record.recipient_id,
            supplier_id=record.supplier_id,
            message_type=record.message_type,
            body=record.body,
            public_link=record.public_link,
            response_token_digest=record.response_token_digest,
            metadata_data=encode_state(self._sanitize_metadata(record.metadata)),
            fingerprint=record.fingerprint,
            status=record.status.value,
            accepted_at=_as_utc(record.accepted_at),
            delivered_at=None,
            version=1,
        )

    def _activity_row(self, activity: ManualDeliveryActivity) -> ManualDeliveryActivityRow:
        deduplication_key = None
        if activity.action in {
            ManualDeliveryAction.LINK_CREATED,
            ManualDeliveryAction.SUPPLIER_OPENED,
        }:
            deduplication_key = f"{activity.external_id}:{activity.action.value}"
        return ManualDeliveryActivityRow(
            activity_id=activity.activity_id,
            external_id=activity.external_id,
            action=activity.action.value,
            actor_id=activity.actor_id,
            channel=activity.channel,
            occurred_at=_as_utc(activity.occurred_at),
            metadata_data=encode_state(self._sanitize_metadata(activity.metadata)),
            deduplication_key=deduplication_key,
        )

    def _to_record(self, row: ManualLinkDeliveryRow) -> ManualLinkDeliveryRecord:
        metadata = decode_state(row.metadata_data)
        if not isinstance(metadata, dict):
            raise PersistenceIntegrityError("manual delivery metadata is not a mapping")
        return ManualLinkDeliveryRecord(
            external_id=row.external_id,
            idempotency_key=row.idempotency_key,
            recipient_id=row.recipient_id,
            supplier_id=row.supplier_id,
            message_type=row.message_type,
            body=row.body,
            public_link=row.public_link,
            response_token_digest=row.response_token_digest,
            metadata=metadata,
            fingerprint=row.fingerprint,
            status=DeliveryState(row.status),
            accepted_at=_from_database_datetime(row.accepted_at),
            delivered_at=(
                _from_database_datetime(row.delivered_at) if row.delivered_at is not None else None
            ),
        )

    def _to_activity(self, row: ManualDeliveryActivityRow) -> ManualDeliveryActivity:
        metadata = decode_state(row.metadata_data)
        if not isinstance(metadata, dict):
            raise PersistenceIntegrityError("manual delivery activity metadata is not a mapping")
        return ManualDeliveryActivity(
            activity_id=row.activity_id,
            external_id=row.external_id,
            action=ManualDeliveryAction(row.action),
            actor_id=row.actor_id,
            channel=row.channel,
            occurred_at=_from_database_datetime(row.occurred_at),
            metadata=metadata,
        )

    def _sanitize_metadata(self, metadata: Mapping[str, Any]) -> dict[str, Any]:
        sanitized: dict[str, Any] = {}
        for raw_key, value in metadata.items():
            key = str(raw_key)
            normalized_key = key.casefold()
            if normalized_key in _SENSITIVE_METADATA_KEYS:
                sanitized[f"{key}_hmac_sha256"] = self._pii_digest(value)
            elif isinstance(value, Mapping):
                sanitized[key] = self._sanitize_metadata(value)
            elif isinstance(value, (list, tuple)):
                sanitized[key] = [
                    self._sanitize_metadata(item) if isinstance(item, Mapping) else item
                    for item in value
                ]
            else:
                sanitized[key] = value
        return sanitized

    def _pii_digest(self, value: Any) -> str:
        normalized = str(value).strip().casefold().encode("utf-8")
        return hmac.new(self._pii_hash_key, normalized, hashlib.sha256).hexdigest()

    @staticmethod
    def _validate_created_pair(
        record: ManualLinkDeliveryRecord,
        activity: ManualDeliveryActivity,
    ) -> None:
        if activity.action != ManualDeliveryAction.LINK_CREATED:
            raise ValueError("manual delivery creation requires a LINK_CREATED activity")
        if activity.external_id != record.external_id:
            raise ValueError("LINK_CREATED activity must reference the new external_id")

    @staticmethod
    def _validate_open_activity(
        external_id: str,
        activity: ManualDeliveryActivity,
    ) -> None:
        if activity.action != ManualDeliveryAction.SUPPLIER_OPENED:
            raise ValueError("delivery confirmation requires a SUPPLIER_OPENED activity")
        if activity.external_id != external_id:
            raise ValueError("SUPPLIER_OPENED activity references another external_id")


def _validate_safe_public_link(public_link: str, external_id: str) -> None:
    parsed = urlsplit(public_link)
    if parsed.scheme != "https" or not parsed.netloc or parsed.fragment:
        raise ValueError("public_link must be an absolute HTTPS capability URL")
    query = parse_qs(parsed.query, keep_blank_values=True)
    if _FORBIDDEN_LINK_QUERY_KEYS.intersection(key.casefold() for key in query):
        raise ValueError("public_link must not contain a raw response token")
    query_values = [item for values in query.values() for item in values]
    path_has_capability = external_id in parsed.path.split("/")
    query_has_capability = external_id in query_values
    if not path_has_capability and not query_has_capability:
        raise ValueError("public_link must contain only the opaque external_id capability")
    if query_values and any(item != external_id for item in query_values):
        raise ValueError("public_link query may contain only the opaque external_id")


def _validate_sha256_digest(value: str, field_name: str) -> None:
    try:
        is_valid = len(value) == 64 and len(bytes.fromhex(value)) == 32
    except ValueError:
        is_valid = False
    if not is_valid:
        raise ValueError(f"{field_name} must be a SHA-256 hex digest")


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("manual delivery timestamps must be timezone-aware")
    return value.astimezone(UTC)


def _from_database_datetime(value: datetime) -> datetime:
    # SQLite discards timezone offsets despite DateTime(timezone=True). Values
    # are written only after UTC normalization, so reattaching UTC is safe.
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


__all__ = ["SqlAlchemyManualLinkDeliveryRepository"]
