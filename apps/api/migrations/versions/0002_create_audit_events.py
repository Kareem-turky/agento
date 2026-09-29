"""Create product.audit_events: durable, append-only, metadata-only audit events.

One row per ``AuditEvent`` recorded by ``ExecutionCoordinator`` through the
``PostgresAuditSink``. No raw parameters, prompts, provider responses, secrets or
headers: only the explicit event fields. No foreign keys (audit is independent of
write commands and covers reads, denials and failed validations too).

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-29
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "product"
TABLE = "audit_events"

# Frozen snapshot of the audit vocabulary at this revision (migrations never import
# app enums; a test keeps these in step with the current contracts).
EVENT_TYPES = (
    "requested", "policy_decided", "denied", "awaiting_approval", "handler_not_registered",
    "validation_failed", "execution_started", "execution_completed", "execution_failed",
    "verification_started", "verified", "requires_human",
)  # fmt: skip
ACTOR_TYPES = ("user", "api_client", "system_agent")
CHANNELS = ("api", "web", "whatsapp", "system")
POLICY_OUTCOMES = ("allow", "deny", "require_approval")
POLICY_REASONS = (
    "unknown_action", "permission_denied", "read_allowed", "low_risk_write_allowed",
    "medium_risk_requires_approval", "high_risk_requires_approval",
)  # fmt: skip
RUN_STATUSES = ("denied", "awaiting_approval", "failed", "requires_human", "verified")
RUN_REASONS = (
    "policy_denied", "approval_required", "audit_unavailable", "handler_not_registered",
    "input_invalid", "handler_contract_violation", "execution_failed_no_effect",
    "execution_outcome_uncertain", "verification_failed", "verification_error",
    "audit_incomplete", "verified",
)  # fmt: skip


def _in(column: str, values: Sequence[str], *, nullable: bool) -> str:
    listed = ", ".join(f"'{v}'" for v in values)
    condition = f"{column} IN ({listed})"
    return f"{column} IS NULL OR {condition}" if nullable else condition


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("request_id", sa.Uuid(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("event_type", sa.String(32), nullable=False),
        sa.Column("action_name", sa.Text(), nullable=False),
        sa.Column("actor_id", sa.Text(), nullable=True),
        sa.Column("actor_type", sa.String(32), nullable=True),
        sa.Column("company_id", sa.Text(), nullable=False),
        sa.Column("store_id", sa.Text(), nullable=True),
        sa.Column("channel", sa.String(32), nullable=False),
        sa.Column("policy_outcome", sa.String(32), nullable=True),
        sa.Column("policy_reason", sa.String(64), nullable=True),
        sa.Column("run_status", sa.String(32), nullable=True),
        sa.Column("run_reason", sa.String(64), nullable=True),
        sa.Column("execution_reference_id", sa.String(128), nullable=True),
        sa.Column("verification_code", sa.String(64), nullable=True),
        sa.Column(
            "recorded_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("event_id", name="pk_audit_events"),
        sa.CheckConstraint(_in("event_type", EVENT_TYPES, nullable=False),
                           name="ck_audit_events_event_type"),
        sa.CheckConstraint(_in("actor_type", ACTOR_TYPES, nullable=True),
                           name="ck_audit_events_actor_type"),
        sa.CheckConstraint(_in("channel", CHANNELS, nullable=False),
                           name="ck_audit_events_channel"),
        sa.CheckConstraint(_in("policy_outcome", POLICY_OUTCOMES, nullable=True),
                           name="ck_audit_events_policy_outcome"),
        sa.CheckConstraint(_in("policy_reason", POLICY_REASONS, nullable=True),
                           name="ck_audit_events_policy_reason"),
        sa.CheckConstraint(_in("run_status", RUN_STATUSES, nullable=True),
                           name="ck_audit_events_run_status"),
        sa.CheckConstraint(_in("run_reason", RUN_REASONS, nullable=True),
                           name="ck_audit_events_run_reason"),
        schema=SCHEMA,
    )  # fmt: skip
    # Forensic lookups: all events of one action run, and of one request.
    op.create_index("ix_audit_events_run_id", TABLE, ["run_id"], schema=SCHEMA)
    op.create_index("ix_audit_events_request_id", TABLE, ["request_id"], schema=SCHEMA)


def downgrade() -> None:
    # Only this table (and its indexes/constraints): never the schema, write_commands,
    # or CASCADE.
    op.drop_index("ix_audit_events_request_id", table_name=TABLE, schema=SCHEMA)
    op.drop_index("ix_audit_events_run_id", table_name=TABLE, schema=SCHEMA)
    op.drop_table(TABLE, schema=SCHEMA)
