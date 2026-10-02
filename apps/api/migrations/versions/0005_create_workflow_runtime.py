"""Create the Workflow Platform runtime state: workflow_runs, workflow_step_runs and
workflow_events (Task 034).

Execution CONTROL state only: run status, the execution claim (lease), per-Step attempts
(every retry is its own row), the minimal typed checkpoint a later Step needs, and an
append-only lifecycle event history. No Workflow DEFINITION, Step definition or handler
registration is stored (they are reviewed Product source code), and no business data:
no order, shipment, customer, inventory or report rows and no provider payload, credential,
model text or exception message. ``workflow_events`` is append-only (enforced by a
trigger); it does not replace ``audit_events``.

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-02
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "product"
RUNS = "workflow_runs"
STEPS = "workflow_step_runs"
EVENTS = "workflow_events"
APPEND_ONLY_FUNCTION = "workflow_events_append_only"
APPEND_ONLY_TRIGGER = "trg_workflow_events_append_only"

# Frozen snapshot of the vocabularies at this revision (migrations never import app
# enums; a test keeps these in step with the current contracts).
RUN_STATUSES = ("pending", "running", "succeeded", "failed", "requires_human",
                "awaiting_approval")  # fmt: skip
TERMINAL_RUN_STATUSES = ("succeeded", "failed", "requires_human", "awaiting_approval")
STEP_STATUSES = ("running", "succeeded", "failed", "timed_out", "requires_human",
                 "awaiting_approval")  # fmt: skip
FAILURE_CODES = (
    "input_invalid", "handler_not_registered", "access_denied", "step_timeout",
    "step_execution_failed", "step_verification_failed", "step_outcome_uncertain",
    "checkpoint_invalid", "retry_exhausted", "executor_lost", "approval_required",
    "workflow_unavailable", "lease_conflict",
)  # fmt: skip
EVENT_TYPES = (
    "workflow_requested", "workflow_started", "workflow_resumed", "step_started",
    "step_succeeded", "step_failed", "step_timed_out", "step_retrying", "workflow_succeeded",
    "workflow_failed", "workflow_requires_human", "workflow_awaiting_approval",
)  # fmt: skip
VERIFICATION_CODES = ("verified", "not_verified")
ACTOR_TYPES = ("user", "api_client", "system_agent")
CHANNELS = ("api", "web", "whatsapp", "system")
DOTTED_ID = "^[a-z][a-z0-9_]*(\\.[a-z][a-z0-9_]*)+$"
STEP_ID = "^[a-z][a-z0-9_]{0,63}$"
MAX_INPUT_BYTES = 4096
MAX_CHECKPOINT_BYTES = 8192


def _in(column: str, values: Sequence[str], *, nullable: bool) -> str:
    listed = ", ".join(f"'{v}'" for v in values)
    condition = f"{column} IN ({listed})"
    return f"{column} IS NULL OR {condition}" if nullable else condition


def upgrade() -> None:
    terminal = ", ".join(f"'{s}'" for s in TERMINAL_RUN_STATUSES)
    op.create_table(
        RUNS,
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("workflow_id", sa.String(128), nullable=False),
        sa.Column("workflow_version", sa.Integer(), nullable=False),
        sa.Column("request_id", sa.Uuid(), nullable=False),
        sa.Column("company_id", sa.Text(), nullable=False),
        sa.Column("actor_id", sa.Text(), nullable=False),
        sa.Column("actor_type", sa.String(32), nullable=False),
        sa.Column("channel", sa.String(32), nullable=False),
        sa.Column("store_id", sa.Text(), nullable=True),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("current_step_id", sa.String(64), nullable=True),
        sa.Column("failure_code", sa.String(64), nullable=True),
        sa.Column("input_state", JSONB(), nullable=False),
        sa.Column("input_fingerprint", sa.String(64), nullable=False),
        sa.Column("lease_owner", sa.Uuid(), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("run_id", name="pk_workflow_runs"),
        sa.CheckConstraint(f"workflow_id ~ '{DOTTED_ID}'", name="ck_workflow_runs_workflow_id"),
        sa.CheckConstraint("workflow_version >= 1", name="ck_workflow_runs_workflow_version"),
        sa.CheckConstraint("char_length(company_id) BETWEEN 1 AND 200",
                           name="ck_workflow_runs_company_id"),
        sa.CheckConstraint("char_length(actor_id) BETWEEN 1 AND 200",
                           name="ck_workflow_runs_actor_id"),
        sa.CheckConstraint("store_id IS NULL OR char_length(store_id) BETWEEN 1 AND 200",
                           name="ck_workflow_runs_store_id"),
        sa.CheckConstraint(_in("actor_type", ACTOR_TYPES, nullable=False),
                           name="ck_workflow_runs_actor_type"),
        sa.CheckConstraint(_in("channel", CHANNELS, nullable=False),
                           name="ck_workflow_runs_channel"),
        sa.CheckConstraint(_in("status", RUN_STATUSES, nullable=False),
                           name="ck_workflow_runs_status"),
        sa.CheckConstraint(f"current_step_id IS NULL OR current_step_id ~ '{STEP_ID}'",
                           name="ck_workflow_runs_current_step_id"),
        sa.CheckConstraint(_in("failure_code", FAILURE_CODES, nullable=True),
                           name="ck_workflow_runs_failure_code"),
        sa.CheckConstraint("jsonb_typeof(input_state) = 'object' AND "
                           f"octet_length(input_state::text) <= {MAX_INPUT_BYTES}",
                           name="ck_workflow_runs_input_state"),
        sa.CheckConstraint("input_fingerprint ~ '^[0-9a-f]{64}$'",
                           name="ck_workflow_runs_input_fingerprint"),
        sa.CheckConstraint("(lease_owner IS NULL) = (lease_expires_at IS NULL)",
                           name="ck_workflow_runs_lease"),
        sa.CheckConstraint(f"(status IN ({terminal})) = (completed_at IS NOT NULL)",
                           name="ck_workflow_runs_completed_at"),
        sa.CheckConstraint(f"status NOT IN ({terminal}) OR lease_owner IS NULL",
                           name="ck_workflow_runs_terminal_lease"),
        sa.CheckConstraint("status <> 'succeeded' OR failure_code IS NULL",
                           name="ck_workflow_runs_succeeded"),
        sa.CheckConstraint("status <> 'failed' OR failure_code IS NOT NULL",
                           name="ck_workflow_runs_failed"),
        sa.CheckConstraint("updated_at >= created_at", name="ck_workflow_runs_updated_at"),
        schema=SCHEMA,
    )  # fmt: skip
    # Run history of one company, newest first.
    op.create_index("ix_workflow_runs_company_id_created_at", RUNS,
                    ["company_id", "created_at", "run_id"], schema=SCHEMA)  # fmt: skip

    op.create_table(
        STEPS,
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("company_id", sa.Text(), nullable=False),
        sa.Column("step_id", sa.String(64), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("handler_id", sa.String(128), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("failure_code", sa.String(64), nullable=True),
        sa.Column("verification_code", sa.String(32), nullable=True),
        sa.Column("checkpoint", JSONB(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("run_id", "step_id", "attempt", name="pk_workflow_step_runs"),
        sa.ForeignKeyConstraint(["run_id"], [f"{SCHEMA}.{RUNS}.run_id"],
                                name="fk_workflow_step_runs_run_id"),
        sa.CheckConstraint(f"step_id ~ '{STEP_ID}'", name="ck_workflow_step_runs_step_id"),
        sa.CheckConstraint("attempt BETWEEN 1 AND 20", name="ck_workflow_step_runs_attempt"),
        sa.CheckConstraint(f"handler_id ~ '{DOTTED_ID}'", name="ck_workflow_step_runs_handler_id"),
        sa.CheckConstraint(_in("status", STEP_STATUSES, nullable=False),
                           name="ck_workflow_step_runs_status"),
        sa.CheckConstraint(_in("failure_code", FAILURE_CODES, nullable=True),
                           name="ck_workflow_step_runs_failure_code"),
        sa.CheckConstraint(_in("verification_code", VERIFICATION_CODES, nullable=True),
                           name="ck_workflow_step_runs_verification_code"),
        sa.CheckConstraint("checkpoint IS NULL OR (jsonb_typeof(checkpoint) = 'object' AND "
                           f"octet_length(checkpoint::text) <= {MAX_CHECKPOINT_BYTES} AND "
                           "status = 'succeeded')", name="ck_workflow_step_runs_checkpoint"),
        sa.CheckConstraint("(status = 'running') = (completed_at IS NULL)",
                           name="ck_workflow_step_runs_completed_at"),
        schema=SCHEMA,
    )  # fmt: skip

    op.create_table(
        EVENTS,
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("company_id", sa.Text(), nullable=False),
        sa.Column("event_type", sa.String(32), nullable=False),
        sa.Column("step_id", sa.String(64), nullable=True),
        sa.Column("attempt", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(32), nullable=True),
        sa.Column("failure_code", sa.String(64), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "recorded_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("run_id", "sequence", name="pk_workflow_events"),
        sa.ForeignKeyConstraint(["run_id"], [f"{SCHEMA}.{RUNS}.run_id"],
                                name="fk_workflow_events_run_id"),
        sa.CheckConstraint("sequence >= 1", name="ck_workflow_events_sequence"),
        sa.CheckConstraint(_in("event_type", EVENT_TYPES, nullable=False),
                           name="ck_workflow_events_event_type"),
        sa.CheckConstraint(f"step_id IS NULL OR step_id ~ '{STEP_ID}'",
                           name="ck_workflow_events_step_id"),
        sa.CheckConstraint("attempt IS NULL OR attempt BETWEEN 1 AND 20",
                           name="ck_workflow_events_attempt"),
        sa.CheckConstraint(_in("status", tuple(sorted({*RUN_STATUSES, *STEP_STATUSES})),
                               nullable=True), name="ck_workflow_events_status"),
        sa.CheckConstraint(_in("failure_code", FAILURE_CODES, nullable=True),
                           name="ck_workflow_events_failure_code"),
        schema=SCHEMA,
    )  # fmt: skip
    # Append-only: events are never updated or deleted (only this migration's downgrade
    # removes the whole table).
    op.execute(
        f"CREATE FUNCTION {SCHEMA}.{APPEND_ONLY_FUNCTION}() RETURNS trigger "
        "LANGUAGE plpgsql AS $$ BEGIN "
        "RAISE EXCEPTION 'workflow_events is append-only'; END $$"
    )
    op.execute(
        f"CREATE TRIGGER {APPEND_ONLY_TRIGGER} BEFORE UPDATE OR DELETE ON {SCHEMA}.{EVENTS} "
        f"FOR EACH ROW EXECUTE FUNCTION {SCHEMA}.{APPEND_ONLY_FUNCTION}()"
    )


def downgrade() -> None:
    # Only the Workflow runtime state: never the schema, write_commands, audit_events,
    # integration_connections, agent_configurations or a cascading drop.
    op.execute(f"DROP TRIGGER {APPEND_ONLY_TRIGGER} ON {SCHEMA}.{EVENTS}")
    op.execute(f"DROP FUNCTION {SCHEMA}.{APPEND_ONLY_FUNCTION}()")
    op.drop_table(EVENTS, schema=SCHEMA)
    op.drop_table(STEPS, schema=SCHEMA)
    op.drop_index("ix_workflow_runs_company_id_created_at", table_name=RUNS, schema=SCHEMA)
    op.drop_table(RUNS, schema=SCHEMA)
