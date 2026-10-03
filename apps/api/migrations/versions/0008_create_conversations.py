"""Create the Product Channel & Conversation transcript (Task 037).

    conversations             one external thread on one IntegrationConnection (unique per
                              company, connection and external conversation reference)
    conversation_messages     the canonical plain-text transcript (Product sequence per
                              conversation; inbound messages deduplicated per company,
                              connection and external message reference)
    message_delivery_events   append-only canonical delivery observations (trigger):
                              every applied transition, plus every stale report that
                              carries a provider event ref (``applied`` = false), so
                              that event identity stays idempotent

NO provider payload, webhook body, header, credential, provider error object or arbitrary
metadata JSON column exists. ``connection_id`` is historical correlation WITHOUT a
foreign key to ``integration_connections``: deleting a connection's metadata or
credentials never deletes conversation history. Migrations 0001-0007 are not touched.
No row is seeded.

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "product"
CONVERSATIONS = "conversations"
MESSAGES = "conversation_messages"
DELIVERY = "message_delivery_events"
APPEND_ONLY_FUNCTION = "message_delivery_events_append_only"
APPEND_ONLY_TRIGGER = "message_delivery_events_append_only"

MAX_TEXT_CHARS = 16000
DIRECTIONS = ("inbound", "outbound")
AUTHOR_KINDS = ("external", "human", "agent", "system")
DELIVERY_STATES = ("received", "pending", "accepted", "sent", "delivered", "failed", "unknown")
UPDATE_STATES = ("accepted", "sent", "delivered", "failed", "unknown")
ACTOR_TYPES = ("user", "api_client", "system_agent")
HASH = "^[0-9a-f]{64}$"
# Opaque external references: 1-256 visible ASCII characters (PostgreSQL regular
# expressions cap a repetition count at 255, so the length is a separate condition).
MAX_REF_CHARS = 256
REF = "^[\\x21-\\x7e]+$"


def _in(column: str, values: Sequence[str], *, nullable: bool) -> str:
    listed = ", ".join(f"'{v}'" for v in values)
    condition = f"{column} IN ({listed})"
    return f"{column} IS NULL OR {condition}" if nullable else condition


def _ref(column: str, *, nullable: bool) -> str:
    condition = f"(char_length({column}) BETWEEN 1 AND {MAX_REF_CHARS} AND {column} ~ '{REF}')"
    return f"{column} IS NULL OR {condition}" if nullable else condition


def upgrade() -> None:
    op.create_table(
        CONVERSATIONS,
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("company_id", sa.Text(), nullable=False),
        sa.Column("store_id", sa.Text(), nullable=True),
        sa.Column("connection_id", sa.Uuid(), nullable=False),
        sa.Column("integration_id", sa.String(64), nullable=False),
        sa.Column("external_conversation_ref", sa.String(256), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_message_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_message_id", sa.Uuid(), nullable=True),
        sa.Column("next_message_sequence", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("conversation_id", name="pk_conversations"),
        sa.UniqueConstraint("company_id", "conversation_id", name="uq_conversations_company"),
        sa.UniqueConstraint("company_id", "connection_id", "external_conversation_ref",
                            name="uq_conversations_external"),
        sa.CheckConstraint("char_length(company_id) BETWEEN 1 AND 256",
                           name="ck_conversations_company_id"),
        sa.CheckConstraint("store_id IS NULL OR char_length(store_id) BETWEEN 1 AND 256",
                           name="ck_conversations_store_id"),
        sa.CheckConstraint("integration_id ~ '^[a-z0-9][a-z0-9-]{0,62}[a-z0-9]$'",
                           name="ck_conversations_integration_id"),
        sa.CheckConstraint(_ref("external_conversation_ref", nullable=False),
                           name="ck_conversations_external_ref"),
        sa.CheckConstraint("next_message_sequence >= 1", name="ck_conversations_sequence"),
        sa.CheckConstraint("(last_message_id IS NULL) = (next_message_sequence = 1)",
                           name="ck_conversations_last_message"),
        schema=SCHEMA,
    )  # fmt: skip
    op.create_index("ix_conversations_company_activity", CONVERSATIONS,
                    ["company_id", "last_message_at", "conversation_id"],
                    schema=SCHEMA)  # fmt: skip

    op.create_table(
        MESSAGES,
        sa.Column("message_id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("company_id", sa.Text(), nullable=False),
        sa.Column("connection_id", sa.Uuid(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("direction", sa.String(16), nullable=False),
        sa.Column("author_kind", sa.String(16), nullable=False),
        sa.Column("external_message_ref", sa.String(256), nullable=True),
        sa.Column("external_sender_ref", sa.String(256), nullable=True),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("content_fingerprint", sa.CHAR(64), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("delivery_state", sa.String(16), nullable=False),
        sa.Column("created_by_actor_id", sa.Text(), nullable=True),
        sa.Column("created_by_actor_type", sa.String(32), nullable=True),
        sa.PrimaryKeyConstraint("message_id", name="pk_conversation_messages"),
        sa.UniqueConstraint("company_id", "message_id", name="uq_conversation_messages_company"),
        sa.UniqueConstraint("conversation_id", "sequence",
                            name="uq_conversation_messages_sequence"),
        sa.UniqueConstraint("company_id", "connection_id", "external_message_ref",
                            name="uq_conversation_messages_external"),
        sa.ForeignKeyConstraint(
            ["company_id", "conversation_id"],
            [f"{SCHEMA}.{CONVERSATIONS}.company_id", f"{SCHEMA}.{CONVERSATIONS}.conversation_id"],
            name="fk_conversation_messages_conversation",
        ),
        sa.CheckConstraint("sequence >= 1", name="ck_conversation_messages_sequence"),
        sa.CheckConstraint(_in("direction", DIRECTIONS, nullable=False),
                           name="ck_conversation_messages_direction"),
        sa.CheckConstraint(_in("author_kind", AUTHOR_KINDS, nullable=False),
                           name="ck_conversation_messages_author_kind"),
        sa.CheckConstraint(_in("delivery_state", DELIVERY_STATES, nullable=False),
                           name="ck_conversation_messages_delivery_state"),
        sa.CheckConstraint(_ref("external_message_ref", nullable=True),
                           name="ck_conversation_messages_external_ref"),
        sa.CheckConstraint(_ref("external_sender_ref", nullable=True),
                           name="ck_conversation_messages_sender_ref"),
        sa.CheckConstraint(f"char_length(text) BETWEEN 1 AND {MAX_TEXT_CHARS}",
                           name="ck_conversation_messages_text"),
        sa.CheckConstraint(f"content_fingerprint ~ '{HASH}'",
                           name="ck_conversation_messages_fingerprint"),
        sa.CheckConstraint("(direction = 'inbound') = (delivery_state = 'received')",
                           name="ck_conversation_messages_inbound_state"),
        sa.CheckConstraint("direction <> 'inbound' OR (author_kind = 'external' AND "
                           "external_message_ref IS NOT NULL AND created_by_actor_id IS NULL)",
                           name="ck_conversation_messages_inbound_source"),
        sa.CheckConstraint("(created_by_actor_id IS NULL) = (created_by_actor_type IS NULL)",
                           name="ck_conversation_messages_creator"),
        sa.CheckConstraint(_in("created_by_actor_type", ACTOR_TYPES, nullable=True),
                           name="ck_conversation_messages_creator_type"),
        schema=SCHEMA,
    )  # fmt: skip

    op.create_table(
        DELIVERY,
        sa.Column("message_id", sa.Uuid(), nullable=False),
        sa.Column("company_id", sa.Text(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("external_event_ref", sa.String(256), nullable=True),
        sa.Column("applied", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("message_id", "sequence", name="pk_message_delivery_events"),
        sa.UniqueConstraint("company_id", "message_id", "external_event_ref",
                            name="uq_message_delivery_events_external"),
        sa.ForeignKeyConstraint(
            ["company_id", "message_id"],
            [f"{SCHEMA}.{MESSAGES}.company_id", f"{SCHEMA}.{MESSAGES}.message_id"],
            name="fk_message_delivery_events_message",
        ),
        sa.CheckConstraint("sequence >= 1", name="ck_message_delivery_events_sequence"),
        sa.CheckConstraint(_in("state", UPDATE_STATES, nullable=False),
                           name="ck_message_delivery_events_state"),
        sa.CheckConstraint(_ref("external_event_ref", nullable=True),
                           name="ck_message_delivery_events_external_ref"),
        sa.CheckConstraint("applied OR external_event_ref IS NOT NULL",
                           name="ck_message_delivery_events_identified"),
        schema=SCHEMA,
    )  # fmt: skip
    op.execute(
        f"CREATE FUNCTION {SCHEMA}.{APPEND_ONLY_FUNCTION}() RETURNS trigger LANGUAGE plpgsql AS "
        "$$ BEGIN RAISE EXCEPTION 'message_delivery_events is append-only'; END $$"
    )
    op.execute(
        f"CREATE TRIGGER {APPEND_ONLY_TRIGGER} BEFORE UPDATE OR DELETE ON {SCHEMA}.{DELIVERY} "
        f"FOR EACH ROW EXECUTE FUNCTION {SCHEMA}.{APPEND_ONLY_FUNCTION}()"
    )


def downgrade() -> None:
    # Only the Task 037 schema: never the schema, an earlier table or a cascading drop.
    op.execute(f"DROP TRIGGER {APPEND_ONLY_TRIGGER} ON {SCHEMA}.{DELIVERY}")
    op.execute(f"DROP FUNCTION {SCHEMA}.{APPEND_ONLY_FUNCTION}()")
    op.drop_table(DELIVERY, schema=SCHEMA)
    op.drop_table(MESSAGES, schema=SCHEMA)
    op.drop_index("ix_conversations_company_activity", table_name=CONVERSATIONS, schema=SCHEMA)
    op.drop_table(CONVERSATIONS, schema=SCHEMA)
