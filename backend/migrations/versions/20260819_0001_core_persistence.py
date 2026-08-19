"""Create aggregate snapshots, append-only audit, idempotency and outbox.

Revision ID: 20260819_0001
Revises:
Create Date: 2026-08-19 15:00:00+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260819_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "aggregate_records",
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("aggregate_type", sa.String(length=64), nullable=False),
        sa.Column("aggregate_id", sa.String(length=128), nullable=False),
        sa.Column("state", sa.String(length=64), nullable=False),
        sa.Column("version", sa.BigInteger(), nullable=False),
        sa.Column(
            "snapshot",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint("version >= 1", name="ck_aggregate_records_version_positive"),
        sa.PrimaryKeyConstraint(
            "tenant_id",
            "aggregate_type",
            "aggregate_id",
            name="pk_aggregate_records",
        ),
    )
    op.create_index(
        "ix_aggregate_records_tenant_type_state",
        "aggregate_records",
        ["tenant_id", "aggregate_type", "state"],
    )

    op.create_table(
        "audit_events",
        sa.Column(
            "position",
            sa.BigInteger(),
            sa.Identity(always=False),
            nullable=False,
        ),
        sa.Column("event_id", sa.String(length=128), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("event_type", sa.String(length=128), nullable=False),
        sa.Column("aggregate_type", sa.String(length=64), nullable=False),
        sa.Column("aggregate_id", sa.String(length=128), nullable=False),
        sa.Column("aggregate_version", sa.BigInteger(), nullable=True),
        sa.Column("actor_type", sa.String(length=32), nullable=False),
        sa.Column("actor_id", sa.String(length=128), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "recorded_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("previous_state", sa.String(length=64), nullable=True),
        sa.Column("new_state", sa.String(length=64), nullable=True),
        sa.Column("correlation_id", sa.String(length=128), nullable=False),
        sa.Column("causation_id", sa.String(length=128), nullable=True),
        sa.Column("agent_run_id", sa.String(length=128), nullable=True),
        sa.Column("idempotency_key", sa.String(length=255), nullable=True),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "aggregate_version IS NULL OR aggregate_version >= 0",
            name="ck_audit_events_aggregate_version_nonnegative",
        ),
        sa.CheckConstraint(
            "actor_type IN ('human', 'supplier', 'agent', 'system', 'external_service')",
            name="ck_audit_events_actor_type_allowed",
        ),
        sa.PrimaryKeyConstraint("position", name="pk_audit_events"),
        sa.UniqueConstraint("event_id", name="uq_audit_events_event_id"),
    )
    op.create_index(
        "ix_audit_events_timeline",
        "audit_events",
        ["tenant_id", "aggregate_type", "aggregate_id", "aggregate_version", "position"],
    )
    op.create_index(
        "ix_audit_events_correlation_id",
        "audit_events",
        ["tenant_id", "correlation_id"],
    )
    op.execute(
        """
        CREATE FUNCTION core_reject_audit_event_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RAISE EXCEPTION 'audit_events is append-only' USING ERRCODE = '55000';
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_audit_events_append_only
        BEFORE UPDATE OR DELETE ON audit_events
        FOR EACH ROW EXECUTE FUNCTION core_reject_audit_event_mutation()
        """
    )

    op.create_table(
        "idempotency_records",
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("operation", sa.String(length=128), nullable=False),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("request_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("outcome_kind", sa.String(length=32), nullable=True),
        sa.Column("response_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("error_code", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('PENDING', 'COMPLETED')",
            name="ck_idempotency_records_status_allowed",
        ),
        sa.CheckConstraint(
            "outcome_kind IS NULL OR outcome_kind IN ('SUCCESS', 'DOMAIN_ERROR')",
            name="ck_idempotency_records_outcome_kind_allowed",
        ),
        sa.PrimaryKeyConstraint(
            "tenant_id",
            "operation",
            "idempotency_key",
            name="pk_idempotency_records",
        ),
    )

    op.create_table(
        "outbox_items",
        sa.Column("id", sa.String(length=128), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("kind", sa.String(length=128), nullable=False),
        sa.Column("aggregate_type", sa.String(length=64), nullable=True),
        sa.Column("aggregate_id", sa.String(length=128), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("payload_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("attempt_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("external_delivery_id", sa.String(length=255), nullable=True),
        sa.Column("locked_by", sa.String(length=128), nullable=True),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('PENDING', 'PROCESSING', 'DELIVERED', 'FAILED')",
            name="ck_outbox_items_status_allowed",
        ),
        sa.CheckConstraint(
            "attempt_count >= 0",
            name="ck_outbox_items_attempt_count_nonnegative",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_outbox_items"),
        sa.UniqueConstraint(
            "tenant_id",
            "idempotency_key",
            name="uq_outbox_tenant_idempotency",
        ),
    )
    op.create_index(
        "ix_outbox_items_due",
        "outbox_items",
        ["status", "next_attempt_at", "created_at"],
    )

    op.create_table(
        "consumed_link_nonces",
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("purpose", sa.String(length=128), nullable=False),
        sa.Column("nonce_hash", sa.String(length=64), nullable=False),
        sa.Column("subject_id", sa.String(length=128), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint(
            "tenant_id",
            "purpose",
            "nonce_hash",
            name="pk_consumed_link_nonces",
        ),
    )
    op.create_index(
        "ix_consumed_link_nonces_expiry",
        "consumed_link_nonces",
        ["expires_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_consumed_link_nonces_expiry", table_name="consumed_link_nonces")
    op.drop_table("consumed_link_nonces")
    op.drop_index("ix_outbox_items_due", table_name="outbox_items")
    op.drop_table("outbox_items")
    op.drop_table("idempotency_records")
    op.execute("DROP TRIGGER trg_audit_events_append_only ON audit_events")
    op.execute("DROP FUNCTION core_reject_audit_event_mutation()")
    op.drop_index("ix_audit_events_correlation_id", table_name="audit_events")
    op.drop_index("ix_audit_events_timeline", table_name="audit_events")
    op.drop_table("audit_events")
    op.drop_index(
        "ix_aggregate_records_tenant_type_state",
        table_name="aggregate_records",
    )
    op.drop_table("aggregate_records")
