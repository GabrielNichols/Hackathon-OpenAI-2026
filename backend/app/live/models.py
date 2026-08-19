"""SQLAlchemy 2.x tables for the durable Dev 4 execution store."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class ExecutionStateRow(Base):
    """One encoded key/value entry from an execution-store bucket."""

    __tablename__ = "dev4_execution_state"

    snapshot_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    bucket: Mapped[str] = mapped_column(String(64), primary_key=True)
    key_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    key_data: Mapped[Any] = mapped_column(JSON, nullable=False)
    value_data: Mapped[Any] = mapped_column(JSON, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )


class ExecutionSnapshotLockRow(Base):
    """Serialization point for all mutations of one execution snapshot."""

    __tablename__ = "dev4_execution_snapshot_lock"

    snapshot_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )


class IdempotencyRow(Base):
    """Immutable operation result bound to one idempotency key."""

    __tablename__ = "dev4_idempotency"

    snapshot_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    operation: Mapped[str] = mapped_column(String(120), primary_key=True)
    idempotency_key: Mapped[str] = mapped_column(String(200), primary_key=True)
    fingerprint: Mapped[str] = mapped_column(String(128), nullable=False)
    result_data: Mapped[Any] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )


class AuditEventRow(Base):
    """Append-only audit event with queryable identity columns."""

    __tablename__ = "dev4_audit_event"

    snapshot_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    event_id: Mapped[str] = mapped_column(String(200), primary_key=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    tenant_id: Mapped[str] = mapped_column(String(200), nullable=False)
    event_type: Mapped[str] = mapped_column(String(160), nullable=False)
    aggregate_type: Mapped[str] = mapped_column(String(120), nullable=False)
    aggregate_id: Mapped[str] = mapped_column(String(200), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    correlation_id: Mapped[str] = mapped_column(String(200), nullable=False)
    event_data: Mapped[Any] = mapped_column(JSON, nullable=False)

    __table_args__ = (
        Index(
            "ix_dev4_audit_tenant_aggregate",
            "snapshot_id",
            "tenant_id",
            "aggregate_type",
            "aggregate_id",
            "position",
        ),
        Index(
            "ix_dev4_audit_correlation",
            "snapshot_id",
            "correlation_id",
            "position",
        ),
    )


class ManualLinkDeliveryRow(Base):
    """Durable individualized manual-link delivery.

    ``public_link`` contains only the opaque external capability id.  The
    internal signed response token is represented exclusively by its digest.
    """

    __tablename__ = "dev4_manual_link_delivery"

    external_id: Mapped[str] = mapped_column(String(200), primary_key=True)
    idempotency_key: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    recipient_id: Mapped[str] = mapped_column(String(200), nullable=False)
    supplier_id: Mapped[str] = mapped_column(String(200), nullable=False)
    message_type: Mapped[str] = mapped_column(String(80), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    public_link: Mapped[str] = mapped_column(Text, nullable=False)
    response_token_digest: Mapped[str] = mapped_column(
        String(64), nullable=False, unique=True
    )
    metadata_data: Mapped[Any] = mapped_column(JSON, nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    accepted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    __table_args__ = (
        CheckConstraint(
            "status IN ('SENT_TO_GATEWAY', 'DELIVERED')",
            name="ck_dev4_manual_link_delivery_status",
        ),
        Index("ix_dev4_manual_delivery_supplier", "supplier_id", "accepted_at"),
    )


class ManualDeliveryActivityRow(Base):
    """Append-only, globally ordered evidence for a manual delivery."""

    __tablename__ = "dev4_manual_delivery_activity"

    sequence_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    activity_id: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    external_id: Mapped[str] = mapped_column(
        ForeignKey("dev4_manual_link_delivery.external_id", ondelete="RESTRICT"),
        nullable=False,
    )
    action: Mapped[str] = mapped_column(String(40), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(200), nullable=False)
    channel: Mapped[str] = mapped_column(String(40), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    metadata_data: Mapped[Any] = mapped_column(JSON, nullable=False)
    deduplication_key: Mapped[str | None] = mapped_column(String(260), unique=True)

    __table_args__ = (
        CheckConstraint(
            "action IN ('LINK_CREATED', 'LINK_COPIED', 'SEND_RECORDED', 'SUPPLIER_OPENED')",
            name="ck_dev4_manual_delivery_activity_action",
        ),
        UniqueConstraint(
            "external_id",
            "sequence_id",
            name="uq_dev4_manual_activity_order",
        ),
        Index("ix_dev4_manual_activity_order", "external_id", "sequence_id"),
    )


__all__ = [
    "AuditEventRow",
    "Base",
    "ExecutionSnapshotLockRow",
    "ExecutionStateRow",
    "IdempotencyRow",
    "ManualDeliveryActivityRow",
    "ManualLinkDeliveryRow",
]
