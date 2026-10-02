"""Create product.agent_configurations: per-installation Product Agent configuration.

One optional OVERRIDE row per (company, installed Product Agent): only the enabled flag
and timestamps. No row means "use the Product AgentDefinition default", so no row is
seeded here. No prompt, instruction, model, provider, credential, tool selection or code
reference is stored, and no Agent runtime state (Agno owns its own tables).

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-02
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "product"
TABLE = "agent_configurations"
AGENT_ID_PATTERN = "^[a-z][a-z0-9-]{0,62}[a-z0-9]$"


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("company_id", sa.Text(), nullable=False),
        sa.Column("agent_id", sa.String(64), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("company_id", "agent_id", name="pk_agent_configurations"),
        sa.CheckConstraint(f"agent_id ~ '{AGENT_ID_PATTERN}'",
                           name="ck_agent_configurations_agent_id"),
        sa.CheckConstraint("char_length(company_id) BETWEEN 1 AND 200",
                           name="ck_agent_configurations_company_id"),
        sa.CheckConstraint("updated_at >= created_at", name="ck_agent_configurations_updated_at"),
        schema=SCHEMA,
    )  # fmt: skip


def downgrade() -> None:
    # Only this table: never the schema, integration_connections, audit_events,
    # write_commands or a cascading drop.
    op.drop_table(TABLE, schema=SCHEMA)
