"""Create the product schema and product.write_commands.

Durable write-command idempotency. Stores only one-way SHA-256 hashes of the
idempotency key and of the request envelope: never the raw key or parameters.

Revision ID: 0001
Revises:
Create Date: 2026-09-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "product"

# Frozen snapshot of the vocabulary at this revision (migrations never import app enums).
STATUSES = (
    "in_progress", "denied", "awaiting_approval", "failed", "requires_human", "verified",
)  # fmt: skip


def upgrade() -> None:
    op.execute(sa.text(f'CREATE SCHEMA IF NOT EXISTS "{SCHEMA}"'))
    statuses = ", ".join(f"'{s}'" for s in STATUSES)
    op.create_table(
        "write_commands",
        sa.Column("command_id", sa.Uuid(), nullable=False),
        sa.Column("company_id", sa.Text(), nullable=False),
        sa.Column("actor_id", sa.Text(), nullable=False),
        sa.Column("store_id", sa.Text(), nullable=True),
        sa.Column("action_name", sa.String(128), nullable=False),
        sa.Column("idempotency_key_hash", sa.CHAR(64), nullable=False),
        sa.Column("request_fingerprint", sa.CHAR(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("reason", sa.String(64), nullable=True),
        sa.Column("action_run_id", sa.Uuid(), nullable=True),
        sa.Column("execution_reference_id", sa.String(128), nullable=True),
        sa.Column("audit_complete", sa.Boolean(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("command_id", name="pk_write_commands"),
        # The one idempotency namespace. Store, action and parameters are deliberately
        # NOT part of it: they are in the fingerprint, so reuse is a conflict.
        sa.UniqueConstraint(
            "company_id", "actor_id", "idempotency_key_hash", name="uq_write_commands_idempotency"
        ),
        sa.CheckConstraint(f"status IN ({statuses})", name="ck_write_commands_status"),
        sa.CheckConstraint(
            "idempotency_key_hash ~ '^[0-9a-f]{64}$'", name="ck_write_commands_key_hash"
        ),
        sa.CheckConstraint(
            "request_fingerprint ~ '^[0-9a-f]{64}$'", name="ck_write_commands_fingerprint"
        ),
        sa.CheckConstraint("reason ~ '^[a-z][a-z0-9_.-]{0,63}$'", name="ck_write_commands_reason"),
        sa.CheckConstraint(
            "(status = 'in_progress') = (reason IS NULL)", name="ck_write_commands_outcome"
        ),
        sa.CheckConstraint(
            "status <> 'verified' OR (action_run_id IS NOT NULL AND audit_complete IS TRUE)",
            name="ck_write_commands_verified",
        ),
        schema=SCHEMA,
    )


def downgrade() -> None:
    # Drops only this table: never the schema (it holds the version table and future
    # product tables) and never CASCADE.
    op.drop_table("write_commands", schema=SCHEMA)
