"""TEST-ONLY Knowledge helpers: an in-memory repository (same contract and semantics as
``PostgresKnowledgeRepository``, with a simple deterministic term-overlap ranking) and a
builder for the real service, gate, coordinator and handlers. Never used by production
code. PostgreSQL behavior is proven in tests/integration/test_knowledge_postgres.py.
"""

import re
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from app.context.models import ActorContext, RequestContext
from app.execution import ActionHandlerRegistry, ExecutionCoordinator
from app.governance import ActionCatalog, GovernanceGate
from app.knowledge.actions import KNOWLEDGE_ACTIONS
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
)
from app.knowledge.errors import KnowledgeRepositoryError
from app.knowledge.handlers import build_knowledge_handlers
from app.knowledge.operating_context import (
    OperatingModelVersion,
    OperatingModelVersionSummary,
    rebuild_version,
    stored_document,
)
from app.knowledge.permissions import KnowledgePermissionEvaluator
from app.knowledge.reader import RepositoryKnowledgeContextReader
from app.knowledge.service import KnowledgeService
from tests.support.integration_fakes import RecordingAuditSink, StepClock

COMPANY = "00000000-0000-4000-8000-00000000c0a1"
OTHER_COMPANY = "00000000-0000-4000-8000-00000000c0b2"
READ = frozenset({"knowledge.read"})
MANAGE = frozenset({"knowledge.read", "knowledge.manage"})


def actor(permissions: frozenset[str] = MANAGE, *, company: str = COMPANY,
          actor_type: str = "user", actor_id: str = "operator-1") -> ActorContext:  # fmt: skip
    return ActorContext(actor_id=actor_id, actor_type=actor_type, company_id=company,
                        permissions=permissions)  # fmt: skip


def request(who: ActorContext | None) -> RequestContext:
    return RequestContext(request_id=uuid4(), actor=who, channel="api")


class InMemoryKnowledgeRepository:
    def __init__(self) -> None:
        self.models: dict[tuple[str, int], dict[str, Any]] = {}
        self.pointers: dict[str, int] = {}
        self.documents: dict[UUID, KnowledgeDocument] = {}
        self.versions: dict[tuple[UUID, int], KnowledgeDocumentVersion] = {}
        self.chunks: dict[tuple[UUID, int], tuple[TextChunk, ...]] = {}
        self.fail = False

    def _check(self) -> None:
        if self.fail:
            raise KnowledgeRepositoryError()

    # operating model
    async def current_operating_model(self, company_id: str) -> OperatingModelVersion | None:
        self._check()
        version = self.pointers.get(company_id)
        return None if version is None else rebuild_version(self.models[(company_id, version)])

    async def operating_model_version(self, company_id: str, version: int):
        self._check()
        row = self.models.get((company_id, version))
        return None if row is None else rebuild_version(row)

    async def operating_model_versions(self, company_id: str):
        self._check()
        current = self.pointers.get(company_id)
        return tuple(
            OperatingModelVersionSummary(version=v, content_hash=row["content_hash"],
                                         created_at=row["created_at"], current=v == current)
            for (c, v), row in sorted(self.models.items(), key=lambda i: -i[0][1])
            if c == company_id
        )  # fmt: skip

    async def publish_operating_model(self, company_id, configuration, content_hash, actor_id,
                                      at) -> int:  # fmt: skip
        self._check()
        version = self.pointers.get(company_id, 0) + 1
        if version > 1 and self.models[(company_id, version - 1)]["content_hash"] == content_hash:
            raise KnowledgeDuplicateError()
        self.models[(company_id, version)] = {
            "company_id": company_id, "version": version,
            "model": stored_document(configuration, company_id, version),
            "content_hash": content_hash,
            "created_by_actor_id": actor_id, "created_at": at,
        }  # fmt: skip
        self.pointers[company_id] = version
        return version

    # documents
    def _owned(self, company_id: str, document_id: UUID) -> KnowledgeDocument | None:
        document = self.documents.get(document_id)
        return document if document is not None and document.company_id == company_id else None

    def _store(self, company_id: str, document_id: UUID, version: int, content: DocumentContent,
               chunks: tuple[TextChunk, ...], actor_id: str, at: datetime) -> None:  # fmt: skip
        self.versions[(document_id, version)] = KnowledgeDocumentVersion(
            document_id=document_id,
            company_id=company_id,
            version=version,
            title=content.title,
            content_type=content.content_type,
            body=content.body,
            content_hash=content.content_hash,
            created_by_actor_id=actor_id,
            created_at=at,
        )
        self.chunks[(document_id, version)] = chunks

    async def create_document(self, company_id, document_id, category, content, chunks,
                              actor_id, at) -> None:  # fmt: skip
        self._check()
        if document_id in self.documents:
            raise KnowledgeConflictError()
        self.documents[document_id] = KnowledgeDocument(
            document_id=document_id,
            company_id=company_id,
            category=category,
            lifecycle=DocumentLifecycle.ACTIVE,
            current_version=1,
            created_at=at,
            updated_at=at,
        )
        self._store(company_id, document_id, 1, content, chunks, actor_id, at)

    async def publish_document_version(self, company_id, document_id, content, chunks, actor_id,
                                       at) -> int:  # fmt: skip
        self._check()
        document = self._owned(company_id, document_id)
        if document is None or document.lifecycle is not DocumentLifecycle.ACTIVE:
            raise KnowledgeConflictError()
        previous = self.versions[(document_id, document.current_version)]
        if previous.content_hash == content.content_hash:
            raise KnowledgeDuplicateError()
        version = document.current_version + 1
        self._store(company_id, document_id, version, content, chunks, actor_id, at)
        self.documents[document_id] = document.model_copy(
            update={"current_version": version, "updated_at": max(document.updated_at, at)}
        )
        return version

    async def archive_document(self, company_id, document_id, at) -> None:
        self._check()
        document = self._owned(company_id, document_id)
        if document is None or document.lifecycle is not DocumentLifecycle.ACTIVE:
            raise KnowledgeConflictError()
        self.documents[document_id] = document.model_copy(
            update={"lifecycle": DocumentLifecycle.ARCHIVED,
                    "updated_at": max(document.updated_at, at)})  # fmt: skip

    async def get_document(self, company_id, document_id):
        self._check()
        return self._owned(company_id, document_id)

    async def list_documents(self, company_id, limit):
        self._check()
        owned = [d for d in self.documents.values() if d.company_id == company_id]
        owned.sort(key=lambda d: (-d.updated_at.timestamp(), str(d.document_id)))
        return tuple(
            DocumentSummary(document=d,
                            title=self.versions[(d.document_id, d.current_version)].title)
            for d in owned[:limit]
        )  # fmt: skip

    async def document_versions(self, company_id, document_id):
        self._check()
        return tuple(
            VersionSummary(version=v.version, title=v.title, content_type=v.content_type.value,
                           content_hash=v.content_hash, created_at=v.created_at)
            for (d, _), v in sorted(self.versions.items(), key=lambda i: -i[0][1])
            if d == document_id and v.company_id == company_id
        )  # fmt: skip

    async def document_version(self, company_id, document_id, version):
        self._check()
        stored = self.versions.get((document_id, version))
        return stored if stored is not None and stored.company_id == company_id else None

    async def chunk_count(self, company_id, document_id, version) -> int:
        self._check()
        if self._owned(company_id, document_id) is None:
            return 0
        return len(self.chunks.get((document_id, version), ()))

    async def search(self, company_id, query, limit):
        self._check()
        terms = set(query_terms(query))
        hits: list[SearchHit] = []
        for document in self.documents.values():
            if document.company_id != company_id or document.lifecycle != "active":
                continue
            version = self.versions[(document.document_id, document.current_version)]
            for chunk in self.chunks[(document.document_id, document.current_version)]:
                words = set(re.findall(r"\w+", chunk.content.lower()))
                rank = float(len(terms & words))
                if rank:
                    hits.append(
                        SearchHit(
                            document_id=document.document_id,
                            document_version=document.current_version,
                            category=KnowledgeCategory(document.category),
                            title=version.title,
                            chunk_index=chunk.index,
                            content=chunk.content,
                            rank=rank,
                        )
                    )
        hits.sort(key=lambda h: (-h.rank, str(h.document_id), h.chunk_index))
        return tuple(hits[:limit])


def build_knowledge_service(
    *, observability: Any = None, installed: frozenset[str] = frozenset({"operations"})
) -> tuple[KnowledgeService, InMemoryKnowledgeRepository, RecordingAuditSink]:
    repository = InMemoryKnowledgeRepository()
    audit = RecordingAuditSink()
    clock = StepClock()
    gate = GovernanceGate(ActionCatalog(KNOWLEDGE_ACTIONS),
                          permissions=KnowledgePermissionEvaluator())  # fmt: skip
    handlers = ActionHandlerRegistry(
        build_knowledge_handlers(repository, repository, installed, clock=clock)
    )
    coordinator = ExecutionCoordinator(gate, handlers, audit)
    reader = RepositoryKnowledgeContextReader(repository, repository, clock=clock)
    service = KnowledgeService(repository, repository, reader, gate, coordinator,
                               installed_agents=installed,
                               observability=observability)  # fmt: skip
    return service, repository, audit
