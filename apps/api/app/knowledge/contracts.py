"""The durable Knowledge state contract (Task 035), implemented by ``app.persistence``.

Every method is one short transaction scoped by the trusted company id IN THE QUERY: a
row of another company is indistinguishable from a missing one. Versions are assigned
by the repository atomically (a locked increment of the current-version pointer), so two
concurrent writers never obtain the same version and no version is half-published.
Every failure is a fixed-message ``KnowledgeRepositoryError``.
"""

from datetime import datetime
from typing import Any, Protocol, runtime_checkable
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.knowledge.chunking import TextChunk
from app.knowledge.documents import (
    DocumentContent,
    KnowledgeCategory,
    KnowledgeDocument,
    KnowledgeDocumentVersion,
)
from app.knowledge.operating_context import OperatingModelVersion, OperatingModelVersionSummary


class KnowledgeDuplicateError(Exception):
    """The new version's content equals the current version's (nothing written)."""


class KnowledgeConflictError(Exception):
    """The target is missing for this company, or is not active (nothing written)."""


class DocumentSummary(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    document: KnowledgeDocument
    title: str  # the current version's title


class VersionSummary(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    version: int
    title: str
    content_type: str
    content_hash: str
    created_at: datetime


class SearchHit(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    document_id: UUID
    document_version: int
    category: KnowledgeCategory
    title: str
    chunk_index: int
    content: str
    rank: float


@runtime_checkable
class OperatingModelRepository(Protocol):
    async def current_operating_model(self, company_id: str) -> OperatingModelVersion | None: ...

    async def operating_model_version(
        self, company_id: str, version: int
    ) -> OperatingModelVersion | None: ...

    async def operating_model_versions(
        self, company_id: str
    ) -> tuple[OperatingModelVersionSummary, ...]: ...

    async def publish_operating_model(
        self, company_id: str, configuration: dict[str, Any], content_hash: str,
        actor_id: str, at: datetime,
    ) -> int:  # fmt: skip
        """Insert the next immutable version and advance the current pointer atomically.
        Raises ``KnowledgeDuplicateError`` when the hash equals the current version's."""
        ...


@runtime_checkable
class KnowledgeDocumentRepository(Protocol):
    async def create_document(
        self, company_id: str, document_id: UUID, category: KnowledgeCategory,
        content: DocumentContent, chunks: tuple[TextChunk, ...], actor_id: str, at: datetime,
    ) -> None: ...  # fmt: skip

    async def publish_document_version(
        self, company_id: str, document_id: UUID, content: DocumentContent,
        chunks: tuple[TextChunk, ...], actor_id: str, at: datetime,
    ) -> int:  # fmt: skip
        """Append the next immutable version (with its chunks) of an ACTIVE document and
        advance its pointer atomically. ``KnowledgeConflictError`` if missing/archived,
        ``KnowledgeDuplicateError`` if identical to the current version."""
        ...

    async def archive_document(self, company_id: str, document_id: UUID, at: datetime) -> None:
        """``KnowledgeConflictError`` if missing or not active."""
        ...

    async def get_document(
        self, company_id: str, document_id: UUID
    ) -> KnowledgeDocument | None: ...

    async def list_documents(self, company_id: str, limit: int) -> tuple[DocumentSummary, ...]: ...

    async def document_versions(
        self, company_id: str, document_id: UUID
    ) -> tuple[VersionSummary, ...]: ...

    async def document_version(
        self, company_id: str, document_id: UUID, version: int
    ) -> KnowledgeDocumentVersion | None: ...

    async def chunk_count(self, company_id: str, document_id: UUID, version: int) -> int: ...

    async def search(self, company_id: str, query: str, limit: int) -> tuple[SearchHit, ...]:
        """Full-text search over the chunks of the CURRENT versions of ACTIVE documents of
        the company, best first with a deterministic tie-break."""
        ...
