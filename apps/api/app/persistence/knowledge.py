"""PostgreSQL implementation of the Knowledge repositories (Task 035).

Tables (owned by migration 0006; never created here), all in the ``product`` schema:

    company_operating_model_versions   immutable versions (append-only trigger)
    company_operating_model_current    one pointer row per company
    knowledge_documents                identity, category, lifecycle, current pointer
    knowledge_document_versions        immutable content snapshots (append-only trigger)
    knowledge_chunks                   immutable chunks of each version, with a stored
                                       generated ``tsvector`` and a GIN index

One short transaction per call; every query is scoped by the trusted company id IN SQL.
Versions are assigned atomically: the pointer row is incremented first (which locks it
until commit), the previous version is compared for duplicates while the lock is held,
then the new immutable rows are inserted; pointer foreign keys are DEFERRED to commit.
Concurrent writers are serialized on the pointer row, so versions are never duplicated
and never skipped visibly (a refused duplicate rolls the increment back).

Retrieval is PostgreSQL full-text search (``english`` configuration) over the chunks of
the CURRENT version of ACTIVE documents of the company: the query is reduced to word
terms (``query_terms``) and bound as ONE parameter; results are ordered by
``ts_rank_cd`` descending, then document id and chunk index (deterministic).

Rows are rebuilt strictly (hashes re-checked); malformed data fails closed. Every
failure is a fixed-message ``KnowledgeRepositoryError`` (exceptions are not chained).
"""

from collections.abc import Mapping
from datetime import datetime
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from pydantic import ValidationError
from sqlalchemy import exc as sa_exc
from sqlalchemy.dialects.postgresql import JSONB, REGCONFIG, TSVECTOR, insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.knowledge.chunking import TextChunk
from app.knowledge.context import query_terms
from app.knowledge.contracts import (
    DocumentSummary,
    KnowledgeConflictError,
    KnowledgeDuplicateError,
    SearchHit,
    VersionSummary,
)
from app.knowledge.documents import (
    DocumentContent,
    DocumentLifecycle,
    KnowledgeCategory,
    KnowledgeDocument,
    KnowledgeDocumentVersion,
    content_hash,
)
from app.knowledge.errors import KnowledgeRepositoryError
from app.knowledge.limits import MAX_RESULTS, MAX_VERSIONS_LISTED
from app.knowledge.operating_context import (
    OperatingModelVersion,
    OperatingModelVersionSummary,
    rebuild_version,
    stored_document,
)
from app.persistence.database import product_metadata

SEARCH_CONFIGURATION = "english"
_HASH = "~ '^[0-9a-f]{64}$'"
_CATEGORIES = ", ".join(f"'{c.value}'" for c in KnowledgeCategory)
_LIFECYCLES = ", ".join(f"'{c.value}'" for c in DocumentLifecycle)
_CONTENT_TYPES = "'text/plain', 'text/markdown'"

# Mirror migration 0006 (``alembic check`` keeps them in step).
company_operating_model_versions = sa.Table(
    "company_operating_model_versions",
    product_metadata,
    sa.Column("company_id", sa.Text(), nullable=False),
    sa.Column("version", sa.Integer(), nullable=False),
    sa.Column("model", JSONB(), nullable=False),
    sa.Column("content_hash", sa.String(64), nullable=False),
    sa.Column("created_by_actor_id", sa.Text(), nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint("company_id", "version", name="pk_company_operating_model_versions"),
    sa.CheckConstraint("version >= 1", name="ck_company_operating_model_versions_version"),
    sa.CheckConstraint(f"content_hash {_HASH}",
                       name="ck_company_operating_model_versions_content_hash"),
    sa.CheckConstraint("char_length(company_id) BETWEEN 1 AND 200",
                       name="ck_company_operating_model_versions_company_id"),
    sa.CheckConstraint("char_length(created_by_actor_id) BETWEEN 1 AND 200",
                       name="ck_company_operating_model_versions_actor"),
    sa.CheckConstraint("jsonb_typeof(model) = 'object'",
                       name="ck_company_operating_model_versions_model"),
)  # fmt: skip

company_operating_model_current = sa.Table(
    "company_operating_model_current",
    product_metadata,
    sa.Column("company_id", sa.Text(), nullable=False),
    sa.Column("current_version", sa.Integer(), nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint("company_id", name="pk_company_operating_model_current"),
    sa.CheckConstraint("current_version >= 1",
                       name="ck_company_operating_model_current_version"),
    sa.ForeignKeyConstraint(
        ["company_id", "current_version"],
        ["product.company_operating_model_versions.company_id",
         "product.company_operating_model_versions.version"],
        name="fk_company_operating_model_current_version",
        deferrable=True, initially="DEFERRED",
    ),
)  # fmt: skip

knowledge_documents = sa.Table(
    "knowledge_documents",
    product_metadata,
    sa.Column("document_id", sa.Uuid(), nullable=False),
    sa.Column("company_id", sa.Text(), nullable=False),
    sa.Column("category", sa.String(32), nullable=False),
    sa.Column("lifecycle", sa.String(16), nullable=False),
    sa.Column("current_version", sa.Integer(), nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint("document_id", name="pk_knowledge_documents"),
    sa.UniqueConstraint("company_id", "document_id", name="uq_knowledge_documents_company"),
    sa.CheckConstraint(f"category IN ({_CATEGORIES})", name="ck_knowledge_documents_category"),
    sa.CheckConstraint(f"lifecycle IN ({_LIFECYCLES})", name="ck_knowledge_documents_lifecycle"),
    sa.CheckConstraint("current_version >= 1", name="ck_knowledge_documents_current_version"),
    sa.CheckConstraint("char_length(company_id) BETWEEN 1 AND 200",
                       name="ck_knowledge_documents_company_id"),
    sa.CheckConstraint("updated_at >= created_at", name="ck_knowledge_documents_updated_at"),
    sa.ForeignKeyConstraint(
        ["document_id", "current_version"],
        ["product.knowledge_document_versions.document_id",
         "product.knowledge_document_versions.version"],
        name="fk_knowledge_documents_current_version",
        deferrable=True, initially="DEFERRED", use_alter=True,
    ),
    sa.Index("ix_knowledge_documents_company_updated", "company_id", "updated_at",
             "document_id"),
)  # fmt: skip

knowledge_document_versions = sa.Table(
    "knowledge_document_versions",
    product_metadata,
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
        ["product.knowledge_documents.company_id", "product.knowledge_documents.document_id"],
        name="fk_knowledge_document_versions_document",
    ),
    sa.CheckConstraint("version >= 1", name="ck_knowledge_document_versions_version"),
    sa.CheckConstraint("char_length(title) BETWEEN 1 AND 200",
                       name="ck_knowledge_document_versions_title"),
    sa.CheckConstraint("char_length(body) BETWEEN 1 AND 50000",
                       name="ck_knowledge_document_versions_body"),
    sa.CheckConstraint(f"content_type IN ({_CONTENT_TYPES})",
                       name="ck_knowledge_document_versions_content_type"),
    sa.CheckConstraint(f"content_hash {_HASH}", name="ck_knowledge_document_versions_hash"),
    sa.CheckConstraint("char_length(created_by_actor_id) BETWEEN 1 AND 200",
                       name="ck_knowledge_document_versions_actor"),
)  # fmt: skip

knowledge_chunks = sa.Table(
    "knowledge_chunks",
    product_metadata,
    sa.Column("document_id", sa.Uuid(), nullable=False),
    sa.Column("version", sa.Integer(), nullable=False),
    sa.Column("chunk_index", sa.Integer(), nullable=False),
    sa.Column("company_id", sa.Text(), nullable=False),
    sa.Column("content", sa.Text(), nullable=False),
    sa.Column("start_offset", sa.Integer(), nullable=False),
    sa.Column("end_offset", sa.Integer(), nullable=False),
    sa.Column("content_hash", sa.String(64), nullable=False),
    sa.Column("search_vector", TSVECTOR(),
              sa.Computed(f"to_tsvector('{SEARCH_CONFIGURATION}'::regconfig, content)",
                          persisted=True), nullable=True),
    sa.PrimaryKeyConstraint("document_id", "version", "chunk_index",
                            name="pk_knowledge_chunks"),
    sa.ForeignKeyConstraint(
        ["document_id", "version"],
        ["product.knowledge_document_versions.document_id",
         "product.knowledge_document_versions.version"],
        name="fk_knowledge_chunks_version",
    ),
    sa.ForeignKeyConstraint(
        ["company_id", "document_id"],
        ["product.knowledge_documents.company_id", "product.knowledge_documents.document_id"],
        name="fk_knowledge_chunks_document",
    ),
    sa.CheckConstraint("chunk_index >= 0", name="ck_knowledge_chunks_index"),
    sa.CheckConstraint("char_length(content) BETWEEN 1 AND 1200",
                       name="ck_knowledge_chunks_content"),
    sa.CheckConstraint("start_offset >= 0 AND end_offset > start_offset",
                       name="ck_knowledge_chunks_offsets"),
    sa.CheckConstraint(f"content_hash {_HASH}", name="ck_knowledge_chunks_hash"),
    sa.Index("ix_knowledge_chunks_search_vector", "search_vector", postgresql_using="gin"),
    sa.Index("ix_knowledge_chunks_company", "company_id", "document_id", "version"),
)  # fmt: skip

_m = company_operating_model_versions.c
_p = company_operating_model_current.c
_d = knowledge_documents.c
_v = knowledge_document_versions.c
_k = knowledge_chunks.c

_ERRORS = (sa_exc.SQLAlchemyError, OSError)


def _document(row: Mapping[str, Any]) -> KnowledgeDocument:
    try:
        return KnowledgeDocument.model_validate(dict(row))
    except (ValidationError, TypeError, ValueError):
        raise KnowledgeRepositoryError() from None


def _version(row: Mapping[str, Any]) -> KnowledgeDocumentVersion:
    try:
        version = KnowledgeDocumentVersion.model_validate(dict(row))
    except (ValidationError, TypeError, ValueError):
        raise KnowledgeRepositoryError() from None
    if content_hash(version.title, version.content_type, version.body) != version.content_hash:
        raise KnowledgeRepositoryError()  # stored content no longer matches its hash
    return version


def _chunk_rows(company_id: str, document_id: UUID, version: int,
                chunks: tuple[TextChunk, ...]) -> list[dict[str, Any]]:  # fmt: skip
    return [
        {"document_id": document_id, "version": version, "chunk_index": c.index,
         "company_id": company_id, "content": c.content, "start_offset": c.start,
         "end_offset": c.end, "content_hash": c.content_hash}
        for c in chunks
    ]  # fmt: skip


def _version_row(company_id: str, document_id: UUID, version: int, content: DocumentContent,
                 actor_id: str, at: datetime) -> dict[str, Any]:  # fmt: skip
    return {"document_id": document_id, "version": version, "company_id": company_id,
            "title": content.title, "content_type": content.content_type.value,
            "body": content.body, "content_hash": content.content_hash,
            "created_by_actor_id": actor_id, "created_at": at}  # fmt: skip


class PostgresKnowledgeRepository:
    """Implements ``OperatingModelRepository`` and ``KnowledgeDocumentRepository``."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = session_factory

    async def _one(self, query: sa.Executable) -> sa.RowMapping | None:
        try:
            async with self._sessions() as session:
                return (await session.execute(query)).mappings().first()
        except _ERRORS:
            raise KnowledgeRepositoryError() from None

    async def _all(self, query: sa.Executable) -> list[sa.RowMapping]:
        try:
            async with self._sessions() as session:
                return list((await session.execute(query)).mappings().all())
        except _ERRORS:
            raise KnowledgeRepositoryError() from None

    # ----- operating model -------------------------------------------------------------------

    async def current_operating_model(self, company_id: str) -> OperatingModelVersion | None:
        query = (
            sa.select(company_operating_model_versions)
            .join(company_operating_model_current,
                  (_p.company_id == _m.company_id) & (_p.current_version == _m.version))
            .where(_p.company_id == company_id, _m.company_id == company_id)
        )  # fmt: skip
        row = await self._one(query)
        return None if row is None else rebuild_version(dict(row))

    async def operating_model_version(
        self, company_id: str, version: int
    ) -> OperatingModelVersion | None:
        query = sa.select(company_operating_model_versions).where(
            _m.company_id == company_id, _m.version == version
        )
        row = await self._one(query)
        return None if row is None else rebuild_version(dict(row))

    async def operating_model_versions(
        self, company_id: str
    ) -> tuple[OperatingModelVersionSummary, ...]:
        query = (
            sa.select(_m.version, _m.content_hash, _m.created_at,
                      sa.func.coalesce(_p.current_version == _m.version, False).label("current"))
            .outerjoin(company_operating_model_current, _p.company_id == _m.company_id)
            .where(_m.company_id == company_id)
            .order_by(_m.version.desc())
            .limit(MAX_VERSIONS_LISTED)
        )  # fmt: skip
        rows = await self._all(query)
        try:
            return tuple(OperatingModelVersionSummary.model_validate(dict(r)) for r in rows)
        except (ValidationError, TypeError, ValueError):
            raise KnowledgeRepositoryError() from None

    async def publish_operating_model(
        self, company_id: str, configuration: dict[str, Any], content_hash: str,
        actor_id: str, at: datetime,
    ) -> int:  # fmt: skip
        advance = (
            insert(company_operating_model_current)
            .values(company_id=company_id, current_version=1, updated_at=at)
            .on_conflict_do_update(
                constraint="pk_company_operating_model_current",
                set_={"current_version": _p.current_version + 1,
                      "updated_at": sa.func.greatest(_p.updated_at, at)},
            )
            .returning(_p.current_version)
        )  # fmt: skip
        try:
            async with self._sessions() as session, session.begin():
                version = (await session.execute(advance)).scalar_one()
                if version > 1:  # the pointer row is locked by this transaction
                    previous = (await session.execute(
                        sa.select(_m.content_hash).where(
                            _m.company_id == company_id, _m.version == version - 1)
                    )).scalar_one_or_none()  # fmt: skip
                    if previous == content_hash:
                        raise KnowledgeDuplicateError()  # rolls the increment back
                await session.execute(sa.insert(company_operating_model_versions).values(
                    company_id=company_id, version=version,
                    model=stored_document(configuration, company_id, version),
                    content_hash=content_hash,
                    created_by_actor_id=actor_id, created_at=at,
                ))  # fmt: skip
        except _ERRORS:
            raise KnowledgeRepositoryError() from None
        return int(version)

    # ----- documents: writes ------------------------------------------------------------------

    async def create_document(
        self, company_id: str, document_id: UUID, category: KnowledgeCategory,
        content: DocumentContent, chunks: tuple[TextChunk, ...], actor_id: str, at: datetime,
    ) -> None:  # fmt: skip
        try:
            async with self._sessions() as session, session.begin():
                await session.execute(sa.insert(knowledge_documents).values(
                    document_id=document_id, company_id=company_id, category=category.value,
                    lifecycle=DocumentLifecycle.ACTIVE.value, current_version=1,
                    created_at=at, updated_at=at,
                ))  # fmt: skip
                await session.execute(
                    sa.insert(knowledge_document_versions).values(
                        _version_row(company_id, document_id, 1, content, actor_id, at)
                    )
                )
                await session.execute(
                    sa.insert(knowledge_chunks), _chunk_rows(company_id, document_id, 1, chunks)
                )
        except sa_exc.IntegrityError:
            raise KnowledgeConflictError() from None  # rolled back: nothing written
        except _ERRORS:
            raise KnowledgeRepositoryError() from None

    async def publish_document_version(
        self, company_id: str, document_id: UUID, content: DocumentContent,
        chunks: tuple[TextChunk, ...], actor_id: str, at: datetime,
    ) -> int:  # fmt: skip
        advance = (
            sa.update(knowledge_documents)
            .where(_d.document_id == document_id, _d.company_id == company_id,
                   _d.lifecycle == DocumentLifecycle.ACTIVE.value)
            .values(current_version=_d.current_version + 1,
                    updated_at=sa.func.greatest(_d.updated_at, at))
            .returning(_d.current_version)
        )  # fmt: skip
        try:
            async with self._sessions() as session, session.begin():
                version = (await session.execute(advance)).scalar_one_or_none()
                if version is None:
                    raise KnowledgeConflictError()  # missing, foreign or archived
                previous = (await session.execute(
                    sa.select(_v.content_hash).where(
                        _v.document_id == document_id, _v.company_id == company_id,
                        _v.version == version - 1)
                )).scalar_one_or_none()  # fmt: skip
                if previous == content.content_hash:
                    raise KnowledgeDuplicateError()  # rolls the increment back
                await session.execute(
                    sa.insert(knowledge_document_versions).values(
                        _version_row(company_id, document_id, version, content, actor_id, at)
                    )
                )
                await session.execute(
                    sa.insert(knowledge_chunks),
                    _chunk_rows(company_id, document_id, version, chunks),
                )
        except _ERRORS:
            raise KnowledgeRepositoryError() from None
        return int(version)

    async def archive_document(self, company_id: str, document_id: UUID, at: datetime) -> None:
        statement = (
            sa.update(knowledge_documents)
            .where(_d.document_id == document_id, _d.company_id == company_id,
                   _d.lifecycle == DocumentLifecycle.ACTIVE.value)
            .values(lifecycle=DocumentLifecycle.ARCHIVED.value,
                    updated_at=sa.func.greatest(_d.updated_at, at))
            .returning(_d.document_id)
        )  # fmt: skip
        try:
            async with self._sessions() as session, session.begin():
                archived = (await session.execute(statement)).scalar_one_or_none()
                if archived is None:
                    raise KnowledgeConflictError()
        except _ERRORS:
            raise KnowledgeRepositoryError() from None

    # ----- documents: reads -------------------------------------------------------------------

    async def get_document(self, company_id: str, document_id: UUID) -> KnowledgeDocument | None:
        row = await self._one(sa.select(knowledge_documents).where(
            _d.document_id == document_id, _d.company_id == company_id))  # fmt: skip
        return None if row is None else _document(dict(row))

    async def list_documents(self, company_id: str, limit: int) -> tuple[DocumentSummary, ...]:
        query = (
            sa.select(knowledge_documents, _v.title)
            .join(knowledge_document_versions,
                  (_v.document_id == _d.document_id) & (_v.version == _d.current_version))
            .where(_d.company_id == company_id, _v.company_id == company_id)
            .order_by(_d.updated_at.desc(), _d.document_id)
            .limit(limit)
        )  # fmt: skip
        rows = await self._all(query)
        summaries: list[DocumentSummary] = []
        for row in rows:
            data = dict(row)
            title = data.pop("title")
            try:
                summaries.append(DocumentSummary(document=_document(data), title=title))
            except (ValidationError, TypeError, ValueError):
                raise KnowledgeRepositoryError() from None
        return tuple(summaries)

    async def document_versions(
        self, company_id: str, document_id: UUID
    ) -> tuple[VersionSummary, ...]:
        query = (
            sa.select(_v.version, _v.title, _v.content_type, _v.content_hash, _v.created_at)
            .where(_v.document_id == document_id, _v.company_id == company_id)
            .order_by(_v.version.desc())
            .limit(MAX_VERSIONS_LISTED)
        )  # fmt: skip
        rows = await self._all(query)
        try:
            return tuple(VersionSummary.model_validate(dict(r)) for r in rows)
        except (ValidationError, TypeError, ValueError):
            raise KnowledgeRepositoryError() from None

    async def document_version(
        self, company_id: str, document_id: UUID, version: int
    ) -> KnowledgeDocumentVersion | None:
        row = await self._one(sa.select(knowledge_document_versions).where(
            _v.document_id == document_id, _v.company_id == company_id,
            _v.version == version))  # fmt: skip
        return None if row is None else _version(dict(row))

    async def chunk_count(self, company_id: str, document_id: UUID, version: int) -> int:
        query = (
            sa.select(sa.func.count())
            .select_from(knowledge_chunks)
            .where(
                _k.document_id == document_id, _k.company_id == company_id, _k.version == version
            )
        )
        try:
            async with self._sessions() as session:
                return int((await session.execute(query)).scalar_one())
        except _ERRORS:
            raise KnowledgeRepositoryError() from None

    async def search(self, company_id: str, query: str, limit: int) -> tuple[SearchHit, ...]:
        terms = query_terms(query)
        if not terms:
            return ()
        # Terms are word characters only; the whole expression is ONE bound parameter.
        expression = " | ".join(f"'{term}'" for term in terms)
        tsquery = sa.func.to_tsquery(sa.cast(SEARCH_CONFIGURATION, REGCONFIG),
                                     sa.bindparam("terms", expression))  # fmt: skip
        rank = sa.func.ts_rank_cd(_k.search_vector, tsquery).label("rank")
        statement = (
            sa.select(_k.document_id, _k.version.label("document_version"), _d.category,
                      _v.title, _k.chunk_index, _k.content, rank)
            .join(knowledge_documents,
                  (_d.document_id == _k.document_id) & (_d.current_version == _k.version))
            .join(knowledge_document_versions,
                  (_v.document_id == _k.document_id) & (_v.version == _k.version))
            .where(
                _k.company_id == company_id, _d.company_id == company_id,
                _v.company_id == company_id,
                _d.lifecycle == DocumentLifecycle.ACTIVE.value,
                _k.search_vector.op("@@")(tsquery),
            )
            .order_by(rank.desc(), _k.document_id, _k.chunk_index)
            .limit(max(1, min(limit, MAX_RESULTS)))
        )  # fmt: skip
        rows = await self._all(statement)
        try:
            return tuple(SearchHit.model_validate(dict(r)) for r in rows)
        except (ValidationError, TypeError, ValueError):
            raise KnowledgeRepositoryError() from None
