"""Create the Product Knowledge & company operating context tables (Task 035).

    company_operating_model_versions   immutable versions of the EXISTING
                                       CompanyOperatingModel (validated JSON + SHA-256)
    company_operating_model_current    one current-version pointer per company
    knowledge_documents                document identity, category, lifecycle, pointer
    knowledge_document_versions        immutable text snapshots (title, type, body, hash)
    knowledge_chunks                   immutable deterministic chunks of each version with
                                       a stored generated tsvector (GIN-indexed; the
                                       language-neutral ``simple`` configuration)

Versions and chunks are append-only (enforced by a trigger). No secret, credential,
binary file, URL, embedding or provider payload is stored; no row is seeded (a company
with zero Knowledge rows is valid).

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-02
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "product"
MODEL_VERSIONS = "company_operating_model_versions"
MODEL_CURRENT = "company_operating_model_current"
DOCUMENTS = "knowledge_documents"
VERSIONS = "knowledge_document_versions"
CHUNKS = "knowledge_chunks"
IMMUTABLE_FUNCTION = "knowledge_reject_mutation"
IMMUTABLE_TABLES = (MODEL_VERSIONS, VERSIONS, CHUNKS)
HASH = "~ '^[0-9a-f]{64}$'"
CATEGORIES = "'sop', 'policy', 'pricing', 'returns', 'shipping', 'supplier', 'general'"
LIFECYCLES = "'active', 'archived'"
CONTENT_TYPES = "'text/plain', 'text/markdown'"


def _trigger(table: str) -> str:
    return f"{table}_immutable"


def upgrade() -> None:
    op.create_table(
        MODEL_VERSIONS,
        sa.Column("company_id", sa.Text(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("model", JSONB(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("created_by_actor_id", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("company_id", "version",
                                name="pk_company_operating_model_versions"),
        sa.CheckConstraint("version >= 1", name="ck_company_operating_model_versions_version"),
        sa.CheckConstraint(f"content_hash {HASH}",
                           name="ck_company_operating_model_versions_content_hash"),
        sa.CheckConstraint("char_length(company_id) BETWEEN 1 AND 200",
                           name="ck_company_operating_model_versions_company_id"),
        sa.CheckConstraint("char_length(created_by_actor_id) BETWEEN 1 AND 200",
                           name="ck_company_operating_model_versions_actor"),
        sa.CheckConstraint("jsonb_typeof(model) = 'object'",
                           name="ck_company_operating_model_versions_model"),
        schema=SCHEMA,
    )  # fmt: skip
    op.create_table(
        MODEL_CURRENT,
        sa.Column("company_id", sa.Text(), nullable=False),
        sa.Column("current_version", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("company_id", name="pk_company_operating_model_current"),
        sa.CheckConstraint("current_version >= 1",
                           name="ck_company_operating_model_current_version"),
        sa.ForeignKeyConstraint(
            ["company_id", "current_version"],
            [f"{SCHEMA}.{MODEL_VERSIONS}.company_id", f"{SCHEMA}.{MODEL_VERSIONS}.version"],
            name="fk_company_operating_model_current_version",
            deferrable=True, initially="DEFERRED",
        ),
        schema=SCHEMA,
    )  # fmt: skip
    op.create_table(
        DOCUMENTS,
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("company_id", sa.Text(), nullable=False),
        sa.Column("category", sa.String(32), nullable=False),
        sa.Column("lifecycle", sa.String(16), nullable=False),
        sa.Column("current_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("document_id", name="pk_knowledge_documents"),
        sa.UniqueConstraint("company_id", "document_id", name="uq_knowledge_documents_company"),
        sa.CheckConstraint(f"category IN ({CATEGORIES})", name="ck_knowledge_documents_category"),
        sa.CheckConstraint(f"lifecycle IN ({LIFECYCLES})",
                           name="ck_knowledge_documents_lifecycle"),
        sa.CheckConstraint("current_version >= 1", name="ck_knowledge_documents_current_version"),
        sa.CheckConstraint("char_length(company_id) BETWEEN 1 AND 200",
                           name="ck_knowledge_documents_company_id"),
        sa.CheckConstraint("updated_at >= created_at", name="ck_knowledge_documents_updated_at"),
        schema=SCHEMA,
    )  # fmt: skip
    op.create_index("ix_knowledge_documents_company_updated", DOCUMENTS,
                    ["company_id", "updated_at", "document_id"], schema=SCHEMA)  # fmt: skip
    op.create_table(
        VERSIONS,
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("company_id", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("content_type", sa.String(32), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("created_by_actor_id", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("document_id", "version", name="pk_knowledge_document_versions"),
        sa.ForeignKeyConstraint(
            ["company_id", "document_id"],
            [f"{SCHEMA}.{DOCUMENTS}.company_id", f"{SCHEMA}.{DOCUMENTS}.document_id"],
            name="fk_knowledge_document_versions_document",
        ),
        sa.CheckConstraint("version >= 1", name="ck_knowledge_document_versions_version"),
        sa.CheckConstraint("char_length(title) BETWEEN 1 AND 200",
                           name="ck_knowledge_document_versions_title"),
        sa.CheckConstraint("char_length(body) BETWEEN 1 AND 50000",
                           name="ck_knowledge_document_versions_body"),
        sa.CheckConstraint(f"content_type IN ({CONTENT_TYPES})",
                           name="ck_knowledge_document_versions_content_type"),
        sa.CheckConstraint(f"content_hash {HASH}", name="ck_knowledge_document_versions_hash"),
        sa.CheckConstraint("char_length(created_by_actor_id) BETWEEN 1 AND 200",
                           name="ck_knowledge_document_versions_actor"),
        schema=SCHEMA,
    )  # fmt: skip
    # The document -> current version pointer is checked at commit (circular reference).
    op.create_foreign_key(
        "fk_knowledge_documents_current_version", DOCUMENTS, VERSIONS,
        ["document_id", "current_version"], ["document_id", "version"],
        source_schema=SCHEMA, referent_schema=SCHEMA, deferrable=True, initially="DEFERRED",
    )  # fmt: skip
    op.create_table(
        CHUNKS,
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("company_id", sa.Text(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("start_offset", sa.Integer(), nullable=False),
        sa.Column("end_offset", sa.Integer(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("search_vector", TSVECTOR(),
                  sa.Computed("to_tsvector('simple'::regconfig, content)", persisted=True),
                  nullable=True),
        sa.PrimaryKeyConstraint("document_id", "version", "chunk_index",
                                name="pk_knowledge_chunks"),
        sa.ForeignKeyConstraint(
            ["document_id", "version"],
            [f"{SCHEMA}.{VERSIONS}.document_id", f"{SCHEMA}.{VERSIONS}.version"],
            name="fk_knowledge_chunks_version",
        ),
        sa.ForeignKeyConstraint(
            ["company_id", "document_id"],
            [f"{SCHEMA}.{DOCUMENTS}.company_id", f"{SCHEMA}.{DOCUMENTS}.document_id"],
            name="fk_knowledge_chunks_document",
        ),
        sa.CheckConstraint("chunk_index >= 0", name="ck_knowledge_chunks_index"),
        sa.CheckConstraint("char_length(content) BETWEEN 1 AND 1200",
                           name="ck_knowledge_chunks_content"),
        sa.CheckConstraint("start_offset >= 0 AND end_offset > start_offset",
                           name="ck_knowledge_chunks_offsets"),
        sa.CheckConstraint(f"content_hash {HASH}", name="ck_knowledge_chunks_hash"),
        schema=SCHEMA,
    )  # fmt: skip
    op.create_index("ix_knowledge_chunks_search_vector", CHUNKS, ["search_vector"],
                    schema=SCHEMA, postgresql_using="gin")  # fmt: skip
    op.create_index("ix_knowledge_chunks_company", CHUNKS,
                    ["company_id", "document_id", "version"], schema=SCHEMA)  # fmt: skip
    op.execute(
        f"CREATE FUNCTION {SCHEMA}.{IMMUTABLE_FUNCTION}() RETURNS trigger LANGUAGE plpgsql AS "
        "$$ BEGIN RAISE EXCEPTION 'knowledge versions are immutable'; END $$"
    )
    for table in IMMUTABLE_TABLES:
        op.execute(
            f"CREATE TRIGGER {_trigger(table)} BEFORE UPDATE OR DELETE ON {SCHEMA}.{table} "
            f"FOR EACH ROW EXECUTE FUNCTION {SCHEMA}.{IMMUTABLE_FUNCTION}()"
        )


def downgrade() -> None:
    # Only the Knowledge tables: never the schema, write_commands, audit_events,
    # integration_connections, agent_configurations, the Workflow tables or a cascade.
    for table in IMMUTABLE_TABLES:
        op.execute(f"DROP TRIGGER {_trigger(table)} ON {SCHEMA}.{table}")
    op.execute(f"DROP FUNCTION {SCHEMA}.{IMMUTABLE_FUNCTION}()")
    op.drop_index("ix_knowledge_chunks_company", table_name=CHUNKS, schema=SCHEMA)
    op.drop_index("ix_knowledge_chunks_search_vector", table_name=CHUNKS, schema=SCHEMA)
    op.drop_table(CHUNKS, schema=SCHEMA)
    op.drop_constraint("fk_knowledge_documents_current_version", DOCUMENTS, type_="foreignkey",
                       schema=SCHEMA)  # fmt: skip
    op.drop_table(VERSIONS, schema=SCHEMA)
    op.drop_index("ix_knowledge_documents_company_updated", table_name=DOCUMENTS, schema=SCHEMA)
    op.drop_table(DOCUMENTS, schema=SCHEMA)
    op.drop_table(MODEL_CURRENT, schema=SCHEMA)
    op.drop_table(MODEL_VERSIONS, schema=SCHEMA)
