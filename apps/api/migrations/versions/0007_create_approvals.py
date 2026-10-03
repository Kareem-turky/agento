"""Create Product human approval state and its correlation columns (Task 036).

    approval_requests   current state of each human-approval request: the exact subject
                        FINGERPRINT (SHA-256), the trusted safe display summary, the
                        human decision and one-time consumption metadata. NO raw action
                        parameters, request body, provider payload, prompt or credential.
    approval_events     append-only lifecycle history (enforced by a trigger)

and nullable ``approval_id`` correlation columns on ``write_commands``, ``audit_events``
and ``workflow_step_runs``. The audit and Workflow-event vocabulary CHECK constraints are
replaced by supersets (migrations 0002 and 0005 are never edited). No row is seeded.

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "product"
REQUESTS = "approval_requests"
EVENTS = "approval_events"
APPEND_ONLY_FUNCTION = "approval_events_append_only"
APPEND_ONLY_TRIGGER = "approval_events_append_only"

STATUSES = ("requested", "approved", "rejected", "expired", "cancelled")
EVENT_TYPES = (*STATUSES, "execution_claimed", "execution_completed")
RISKS = ("medium_risk", "high_risk")
ACTOR_TYPES = ("user", "api_client", "system_agent")
SOURCES = ("action", "write_command", "workflow_step")
OUTCOMES = ("verified", "failed", "requires_human")
MAX_NOTE_CHARS = 1000
MAX_SUMMARY_BYTES = 16384
HASH = "^[0-9a-f]{64}$"

# The 0002 audit vocabularies (frozen there) and their Task 036 supersets.
AUDIT_EVENT_TYPES_0002 = (
    "requested", "policy_decided", "denied", "awaiting_approval", "handler_not_registered",
    "validation_failed", "execution_started", "execution_completed", "execution_failed",
    "verification_started", "verified", "requires_human",
)  # fmt: skip
AUDIT_EVENT_TYPES = (*AUDIT_EVENT_TYPES_0002, "approval_refused")
RUN_REASONS_0002 = (
    "policy_denied", "approval_required", "audit_unavailable", "handler_not_registered",
    "input_invalid", "handler_contract_violation", "execution_failed_no_effect",
    "execution_outcome_uncertain", "verification_failed", "verification_error",
    "audit_incomplete", "verified",
)  # fmt: skip
RUN_REASONS = (
    *RUN_REASONS_0002, "approval_unavailable", "approval_not_found", "approval_not_decided",
    "approval_rejected", "approval_expired", "approval_cancelled", "approval_mismatch",
    "approval_already_consumed",
)  # fmt: skip
# The 0005 Workflow event vocabulary (frozen there) and its Task 036 superset.
WORKFLOW_EVENT_TYPES_0005 = (
    "workflow_requested", "workflow_started", "workflow_resumed", "step_started",
    "step_succeeded", "step_failed", "step_timed_out", "step_retrying", "workflow_succeeded",
    "workflow_failed", "workflow_requires_human", "workflow_awaiting_approval",
)  # fmt: skip
WORKFLOW_EVENT_TYPES = (*WORKFLOW_EVENT_TYPES_0005, "workflow_approval_resumed")


def _in(column: str, values: Sequence[str], *, nullable: bool) -> str:
    listed = ", ".join(f"'{v}'" for v in values)
    condition = f"{column} IN ({listed})"
    return f"{column} IS NULL OR {condition}" if nullable else condition


def _replace_check(table: str, name: str, condition: str, *, restore: bool = False) -> None:
    """Swap one CHECK constraint. On downgrade (``restore``) rows written under the
    newer vocabulary are kept: if any exists the narrower constraint is added NOT VALID
    (enforced for new rows only) instead of deleting audit history."""
    op.drop_constraint(name, table, type_="check", schema=SCHEMA)
    if restore:
        bind = op.get_bind()
        violating = bind.execute(sa.text(
            f"SELECT count(*) FROM {SCHEMA}.{table} WHERE NOT ({condition})"  # noqa: S608
        )).scalar_one()  # fmt: skip
        if violating:
            op.execute(f"ALTER TABLE {SCHEMA}.{table} ADD CONSTRAINT {name} "
                       f"CHECK ({condition}) NOT VALID")  # fmt: skip
            return
    op.create_check_constraint(name, table, condition, schema=SCHEMA)


def upgrade() -> None:
    op.create_table(
        REQUESTS,
        sa.Column("approval_id", sa.Uuid(), nullable=False),
        sa.Column("company_id", sa.Text(), nullable=False),
        sa.Column("store_id", sa.Text(), nullable=True),
        sa.Column("action_name", sa.String(128), nullable=False),
        sa.Column("risk", sa.String(32), nullable=False),
        sa.Column("requester_actor_id", sa.Text(), nullable=False),
        sa.Column("requester_actor_type", sa.String(32), nullable=False),
        sa.Column("request_id", sa.Uuid(), nullable=False),
        sa.Column("action_run_id", sa.Uuid(), nullable=False),
        sa.Column("subject_fingerprint", sa.CHAR(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("summary", JSONB(), nullable=False),
        sa.Column("source_kind", sa.String(32), nullable=False),
        sa.Column("source_command_id", sa.Uuid(), nullable=True),
        sa.Column("source_workflow_run_id", sa.Uuid(), nullable=True),
        sa.Column("source_workflow_id", sa.String(128), nullable=True),
        sa.Column("source_workflow_step_id", sa.String(128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decided_by_actor_id", sa.Text(), nullable=True),
        sa.Column("decided_by_actor_type", sa.String(32), nullable=True),
        sa.Column("decision_note", sa.Text(), nullable=True),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("consumed_by_action_run_id", sa.Uuid(), nullable=True),
        sa.Column("execution_outcome", sa.String(32), nullable=True),
        sa.PrimaryKeyConstraint("approval_id", name="pk_approval_requests"),
        sa.UniqueConstraint("company_id", "approval_id", name="uq_approval_requests_company"),
        sa.CheckConstraint("char_length(company_id) BETWEEN 1 AND 200",
                           name="ck_approval_requests_company_id"),
        sa.CheckConstraint("store_id IS NULL OR char_length(store_id) BETWEEN 1 AND 200",
                           name="ck_approval_requests_store_id"),
        sa.CheckConstraint(_in("risk", RISKS, nullable=False), name="ck_approval_requests_risk"),
        sa.CheckConstraint(_in("requester_actor_type", ACTOR_TYPES, nullable=False),
                           name="ck_approval_requests_requester_type"),
        sa.CheckConstraint(f"subject_fingerprint ~ '{HASH}'",
                           name="ck_approval_requests_fingerprint"),
        sa.CheckConstraint(_in("status", STATUSES, nullable=False),
                           name="ck_approval_requests_status"),
        sa.CheckConstraint(_in("source_kind", SOURCES, nullable=False),
                           name="ck_approval_requests_source_kind"),
        sa.CheckConstraint("jsonb_typeof(summary) = 'object' AND "
                           f"octet_length(summary::text) <= {MAX_SUMMARY_BYTES}",
                           name="ck_approval_requests_summary"),
        sa.CheckConstraint("expires_at > created_at", name="ck_approval_requests_expires_at"),
        sa.CheckConstraint("(status = 'requested') = (decided_at IS NULL)",
                           name="ck_approval_requests_decided_at"),
        sa.CheckConstraint("(status IN ('approved', 'rejected', 'cancelled')) = "
                           "(decided_by_actor_id IS NOT NULL)",
                           name="ck_approval_requests_decider"),
        sa.CheckConstraint("(decided_by_actor_id IS NULL) = (decided_by_actor_type IS NULL)",
                           name="ck_approval_requests_decider_type"),
        sa.CheckConstraint(_in("decided_by_actor_type", ("user", "api_client"), nullable=True),
                           name="ck_approval_requests_human_decider"),
        sa.CheckConstraint("status NOT IN ('approved', 'rejected') OR "
                           "decided_by_actor_id <> requester_actor_id",
                           name="ck_approval_requests_two_person"),
        sa.CheckConstraint("status NOT IN ('rejected', 'cancelled') OR "
                           "decision_note IS NOT NULL", name="ck_approval_requests_reason"),
        sa.CheckConstraint(f"decision_note IS NULL OR char_length(decision_note) BETWEEN 1 AND "
                           f"{MAX_NOTE_CHARS}", name="ck_approval_requests_note"),
        sa.CheckConstraint("consumed_at IS NULL OR status = 'approved'",
                           name="ck_approval_requests_consumed"),
        sa.CheckConstraint("(consumed_at IS NULL) = (consumed_by_action_run_id IS NULL)",
                           name="ck_approval_requests_consumed_by"),
        sa.CheckConstraint("(" + _in("execution_outcome", OUTCOMES, nullable=True) +
                           ") AND (execution_outcome IS NULL OR consumed_at IS NOT NULL)",
                           name="ck_approval_requests_outcome"),
        schema=SCHEMA,
    )  # fmt: skip
    op.create_index("ix_approval_requests_company_created", REQUESTS,
                    ["company_id", "created_at", "approval_id"], schema=SCHEMA)  # fmt: skip
    op.create_index("ix_approval_requests_company_due", REQUESTS,
                    ["company_id", "status", "expires_at"], schema=SCHEMA)  # fmt: skip
    op.create_table(
        EVENTS,
        sa.Column("approval_id", sa.Uuid(), nullable=False),
        sa.Column("company_id", sa.Text(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(32), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("actor_id", sa.Text(), nullable=True),
        sa.Column("actor_type", sa.String(32), nullable=True),
        sa.Column("action_run_id", sa.Uuid(), nullable=True),
        sa.Column("execution_outcome", sa.String(32), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
                  nullable=False),
        sa.PrimaryKeyConstraint("approval_id", "sequence", name="pk_approval_events"),
        sa.ForeignKeyConstraint(
            ["company_id", "approval_id"],
            [f"{SCHEMA}.{REQUESTS}.company_id", f"{SCHEMA}.{REQUESTS}.approval_id"],
            name="fk_approval_events_request",
        ),
        sa.CheckConstraint("sequence >= 1", name="ck_approval_events_sequence"),
        sa.CheckConstraint(_in("event_type", EVENT_TYPES, nullable=False),
                           name="ck_approval_events_event_type"),
        sa.CheckConstraint(_in("status", STATUSES, nullable=False),
                           name="ck_approval_events_status"),
        sa.CheckConstraint(_in("actor_type", ACTOR_TYPES, nullable=True),
                           name="ck_approval_events_actor_type"),
        sa.CheckConstraint(_in("execution_outcome", OUTCOMES, nullable=True),
                           name="ck_approval_events_outcome"),
        schema=SCHEMA,
    )  # fmt: skip
    op.execute(
        f"CREATE FUNCTION {SCHEMA}.{APPEND_ONLY_FUNCTION}() RETURNS trigger LANGUAGE plpgsql AS "
        "$$ BEGIN RAISE EXCEPTION 'approval_events is append-only'; END $$"
    )
    op.execute(
        f"CREATE TRIGGER {APPEND_ONLY_TRIGGER} BEFORE UPDATE OR DELETE ON {SCHEMA}.{EVENTS} "
        f"FOR EACH ROW EXECUTE FUNCTION {SCHEMA}.{APPEND_ONLY_FUNCTION}()"
    )

    # Correlation only (authorization metadata, never business intent or payload).
    for table in ("write_commands", "audit_events", "workflow_step_runs"):
        op.add_column(table, sa.Column("approval_id", sa.Uuid(), nullable=True), schema=SCHEMA)
    _replace_check("audit_events", "ck_audit_events_event_type",
                   _in("event_type", AUDIT_EVENT_TYPES, nullable=False))  # fmt: skip
    _replace_check("audit_events", "ck_audit_events_run_reason",
                   _in("run_reason", RUN_REASONS, nullable=True))  # fmt: skip
    _replace_check("workflow_events", "ck_workflow_events_event_type",
                   _in("event_type", WORKFLOW_EVENT_TYPES, nullable=False))  # fmt: skip


def downgrade() -> None:
    # Only the Task 036 schema: never the schema, an earlier table or a cascading drop.
    _replace_check("workflow_events", "ck_workflow_events_event_type",
                   _in("event_type", WORKFLOW_EVENT_TYPES_0005, nullable=False),
                   restore=True)  # fmt: skip
    _replace_check("audit_events", "ck_audit_events_run_reason",
                   _in("run_reason", RUN_REASONS_0002, nullable=True), restore=True)  # fmt: skip
    _replace_check("audit_events", "ck_audit_events_event_type",
                   _in("event_type", AUDIT_EVENT_TYPES_0002, nullable=False),
                   restore=True)  # fmt: skip
    for table in ("workflow_step_runs", "audit_events", "write_commands"):
        op.drop_column(table, "approval_id", schema=SCHEMA)
    op.execute(f"DROP TRIGGER {APPEND_ONLY_TRIGGER} ON {SCHEMA}.{EVENTS}")
    op.execute(f"DROP FUNCTION {SCHEMA}.{APPEND_ONLY_FUNCTION}()")
    op.drop_table(EVENTS, schema=SCHEMA)
    op.drop_index("ix_approval_requests_company_due", table_name=REQUESTS, schema=SCHEMA)
    op.drop_index("ix_approval_requests_company_created", table_name=REQUESTS, schema=SCHEMA)
    op.drop_table(REQUESTS, schema=SCHEMA)
