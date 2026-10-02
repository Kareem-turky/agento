"""Create product.integration_connections: Product-owned integration connection METADATA.

How this installation connects to an installed integration type: identity, company,
display name, NON-secret configuration, the NAMES of configured secret fields, the
enabled flag and the last-known connection test state. No secret value (those live in
the integration secret store, outside PostgreSQL) and no business data (source systems
stay authoritative; nothing is mirrored).

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-02
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "product"
TABLE = "integration_connections"
INDEX = "ix_integration_connections_company_id_created_at"

# Frozen snapshots of the vocabularies at this revision (migrations never import app
# enums; a test keeps these in step with the current contracts).
TEST_RESULTS = ("never_tested", "success", "failure")
TEST_ERRORS = (
    "authentication_failed", "permission_denied", "unreachable", "timeout",
    "invalid_configuration", "credentials_unavailable", "unexpected_response",
    "provider_error",
)  # fmt: skip
INTEGRATION_ID_PATTERN = "^[a-z0-9][a-z0-9-]{0,62}[a-z0-9]$"


def _in(column: str, values: Sequence[str]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("connection_id", sa.Uuid(), nullable=False),
        sa.Column("company_id", sa.Text(), nullable=False),
        sa.Column("integration_id", sa.String(64), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("config", postgresql.JSONB(), nullable=False),
        sa.Column("secret_fields", postgresql.JSONB(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_tested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_test_result", sa.String(16), nullable=False),
        sa.Column("last_test_error", sa.String(64), nullable=True),
        sa.PrimaryKeyConstraint("connection_id", name="pk_integration_connections"),
        sa.CheckConstraint(f"integration_id ~ '{INTEGRATION_ID_PATTERN}'",
                           name="ck_integration_connections_integration_id"),
        sa.CheckConstraint("char_length(display_name) BETWEEN 1 AND 120",
                           name="ck_integration_connections_display_name"),
        sa.CheckConstraint("jsonb_typeof(config) = 'object'",
                           name="ck_integration_connections_config"),
        sa.CheckConstraint("jsonb_typeof(secret_fields) = 'array'",
                           name="ck_integration_connections_secret_fields"),
        sa.CheckConstraint(_in("last_test_result", TEST_RESULTS),
                           name="ck_integration_connections_last_test_result"),
        sa.CheckConstraint(f"last_test_error IS NULL OR {_in('last_test_error', TEST_ERRORS)}",
                           name="ck_integration_connections_last_test_error"),
        sa.CheckConstraint("(last_test_result = 'never_tested') = (last_tested_at IS NULL)",
                           name="ck_integration_connections_tested_at"),
        sa.CheckConstraint("(last_test_result = 'failure') = (last_test_error IS NOT NULL)",
                           name="ck_integration_connections_test_error"),
        schema=SCHEMA,
    )  # fmt: skip
    # Listing a company's connections in creation order.
    op.create_index(INDEX, TABLE, ["company_id", "created_at", "connection_id"], schema=SCHEMA)


def downgrade() -> None:
    # Only this table (and its index/constraints): never the schema, write_commands,
    # audit_events or a cascading drop.
    op.drop_index(INDEX, table_name=TABLE, schema=SCHEMA)
    op.drop_table(TABLE, schema=SCHEMA)
