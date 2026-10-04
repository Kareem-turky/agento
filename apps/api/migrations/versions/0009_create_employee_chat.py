"""Create Employee Chat (Task 042).

    chat_threads            one employee's private chat with the Operations Agent: owned
                            by company + actor (id AND type) + store, agent fixed to
                            ``operations``; Product turn sequence
    chat_turns              one idempotent user turn (client-generated ``turn_id``) and its
                            assistant answer: pending -> completed | failed
    chat_action_proposals   at most ONE typed ticket proposal per turn
                            (``operations.ticket.create`` only): proposed -> confirming
                            (one Idempotency-Key, by its SHA-256) -> submitted (the durable
                            WriteCommand id) | cancelled. A proposal executes nothing.

Employee Chat is NOT the external Conversation transcript (0008): no provider, channel or
customer data and no arbitrary JSON column exists. ``command_id`` correlates with the
existing WriteCommand without a foreign key (the command layer owns its rows).
Migrations 0001-0008 are not touched. No row is seeded.

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-04
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "product"
THREADS = "chat_threads"
TURNS = "chat_turns"
PROPOSALS = "chat_action_proposals"

ACTOR_TYPES = ("user", "api_client", "system_agent")
TURN_STATUSES = ("pending", "completed", "failed")
TURN_FAILURES = ("agent_disabled", "run_failed")
PROPOSAL_STATES = ("proposed", "confirming", "submitted", "cancelled")
MAX_MESSAGE_CHARS = 8000
MAX_ASSISTANT_CHARS = 16000
MAX_TITLE_CHARS = 160
MAX_DESCRIPTION_CHARS = 4000
HASH = "^[0-9a-f]{64}$"


def _in(column: str, values: Sequence[str], *, nullable: bool) -> str:
    listed = ", ".join(f"'{v}'" for v in values)
    condition = f"{column} IN ({listed})"
    return f"{column} IS NULL OR {condition}" if nullable else condition


def _scoped(column: str) -> str:
    return f"char_length({column}) BETWEEN 1 AND 256"


def upgrade() -> None:
    op.create_table(
        THREADS,
        sa.Column("thread_id", sa.Uuid(), nullable=False),
        sa.Column("company_id", sa.Text(), nullable=False),
        sa.Column("actor_id", sa.Text(), nullable=False),
        sa.Column("actor_type", sa.String(32), nullable=False),
        sa.Column("store_id", sa.Text(), nullable=False),
        sa.Column("agent_id", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("next_turn_sequence", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("thread_id", name="pk_chat_threads"),
        sa.UniqueConstraint("company_id", "thread_id", name="uq_chat_threads_company"),
        sa.CheckConstraint(_scoped("company_id"), name="ck_chat_threads_company_id"),
        sa.CheckConstraint(_scoped("actor_id"), name="ck_chat_threads_actor_id"),
        sa.CheckConstraint(_in("actor_type", ACTOR_TYPES, nullable=False),
                           name="ck_chat_threads_actor_type"),
        sa.CheckConstraint(_scoped("store_id"), name="ck_chat_threads_store_id"),
        sa.CheckConstraint("agent_id = 'operations'", name="ck_chat_threads_agent_id"),
        sa.CheckConstraint("next_turn_sequence >= 1", name="ck_chat_threads_sequence"),
        sa.CheckConstraint("updated_at >= created_at", name="ck_chat_threads_times"),
        schema=SCHEMA,
    )  # fmt: skip
    op.create_index("ix_chat_threads_owner", THREADS,
                    ["company_id", "actor_id", "actor_type", "store_id", "updated_at"],
                    schema=SCHEMA)  # fmt: skip

    op.create_table(
        TURNS,
        sa.Column("turn_id", sa.Uuid(), nullable=False),
        sa.Column("thread_id", sa.Uuid(), nullable=False),
        sa.Column("company_id", sa.Text(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("user_text", sa.Text(), nullable=False),
        sa.Column("assistant_text", sa.Text(), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("failure", sa.String(32), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("turn_id", name="pk_chat_turns"),
        sa.UniqueConstraint("company_id", "turn_id", name="uq_chat_turns_company"),
        sa.UniqueConstraint("thread_id", "sequence", name="uq_chat_turns_sequence"),
        sa.ForeignKeyConstraint(
            ["company_id", "thread_id"],
            [f"{SCHEMA}.{THREADS}.company_id", f"{SCHEMA}.{THREADS}.thread_id"],
            name="fk_chat_turns_thread",
        ),
        sa.CheckConstraint("sequence >= 1", name="ck_chat_turns_sequence"),
        sa.CheckConstraint(f"char_length(user_text) BETWEEN 1 AND {MAX_MESSAGE_CHARS}",
                           name="ck_chat_turns_user_text"),
        sa.CheckConstraint(f"assistant_text IS NULL OR char_length(assistant_text) <= "
                           f"{MAX_ASSISTANT_CHARS}", name="ck_chat_turns_assistant_text"),
        sa.CheckConstraint(_in("status", TURN_STATUSES, nullable=False),
                           name="ck_chat_turns_status"),
        sa.CheckConstraint(_in("failure", TURN_FAILURES, nullable=True),
                           name="ck_chat_turns_failure"),
        sa.CheckConstraint("(status = 'completed') = (assistant_text IS NOT NULL)",
                           name="ck_chat_turns_answer"),
        sa.CheckConstraint("(status = 'failed') = (failure IS NOT NULL)",
                           name="ck_chat_turns_failed"),
        sa.CheckConstraint("(status = 'pending') = (completed_at IS NULL)",
                           name="ck_chat_turns_completed_at"),
        schema=SCHEMA,
    )  # fmt: skip

    op.create_table(
        PROPOSALS,
        sa.Column("proposal_id", sa.Uuid(), nullable=False),
        sa.Column("thread_id", sa.Uuid(), nullable=False),
        sa.Column("turn_id", sa.Uuid(), nullable=False),
        sa.Column("company_id", sa.Text(), nullable=False),
        sa.Column("action_name", sa.String(64), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("confirm_key_hash", sa.CHAR(64), nullable=True),
        sa.Column("command_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("proposal_id", name="pk_chat_action_proposals"),
        sa.UniqueConstraint("company_id", "proposal_id", name="uq_chat_action_proposals_company"),
        sa.UniqueConstraint("turn_id", name="uq_chat_action_proposals_turn"),
        sa.ForeignKeyConstraint(
            ["company_id", "turn_id"],
            [f"{SCHEMA}.{TURNS}.company_id", f"{SCHEMA}.{TURNS}.turn_id"],
            name="fk_chat_action_proposals_turn",
        ),
        sa.ForeignKeyConstraint(
            ["company_id", "thread_id"],
            [f"{SCHEMA}.{THREADS}.company_id", f"{SCHEMA}.{THREADS}.thread_id"],
            name="fk_chat_action_proposals_thread",
        ),
        sa.CheckConstraint("action_name = 'operations.ticket.create'",
                           name="ck_chat_action_proposals_action"),
        sa.CheckConstraint(f"char_length(title) BETWEEN 1 AND {MAX_TITLE_CHARS}",
                           name="ck_chat_action_proposals_title"),
        sa.CheckConstraint(f"char_length(description) BETWEEN 1 AND {MAX_DESCRIPTION_CHARS}",
                           name="ck_chat_action_proposals_description"),
        sa.CheckConstraint(_in("state", PROPOSAL_STATES, nullable=False),
                           name="ck_chat_action_proposals_state"),
        sa.CheckConstraint(f"confirm_key_hash IS NULL OR confirm_key_hash ~ '{HASH}'",
                           name="ck_chat_action_proposals_key_hash"),
        sa.CheckConstraint("(state IN ('confirming', 'submitted')) = "
                           "(confirm_key_hash IS NOT NULL)",
                           name="ck_chat_action_proposals_claim"),
        sa.CheckConstraint("(state = 'submitted') = (command_id IS NOT NULL)",
                           name="ck_chat_action_proposals_command"),
        schema=SCHEMA,
    )  # fmt: skip
    op.create_index("ix_chat_action_proposals_thread", PROPOSALS, ["company_id", "thread_id"],
                    schema=SCHEMA)  # fmt: skip


def downgrade() -> None:
    # Only the Task 042 schema: never the schema, an earlier table or a cascading drop.
    op.drop_index("ix_chat_action_proposals_thread", table_name=PROPOSALS, schema=SCHEMA)
    op.drop_table(PROPOSALS, schema=SCHEMA)
    op.drop_table(TURNS, schema=SCHEMA)
    op.drop_index("ix_chat_threads_owner", table_name=THREADS, schema=SCHEMA)
    op.drop_table(THREADS, schema=SCHEMA)
