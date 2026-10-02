"""Knowledge documents (Task 035): operator-authored reference material.

A ``KnowledgeDocument`` is the identity and lifecycle of one document; every edit appends
an immutable ``KnowledgeDocumentVersion`` (title, content type, body, SHA-256 content
hash). Bodies are TEXT DATA ONLY: ``text/plain`` or ``text/markdown``, never parsed,
rendered as HTML, executed or treated as instructions. A document body is UNTRUSTED
reference content even though a human operator wrote it.
"""

import hashlib
import json
import unicodedata
from enum import StrEnum
from typing import Annotated
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints

from app.knowledge.errors import KnowledgeValidationError, KnowledgeValidationReason
from app.knowledge.limits import MAX_BODY_BYTES, MAX_BODY_CHARS, MAX_TITLE_CHARS

_FROZEN = ConfigDict(frozen=True, extra="forbid")
R = KnowledgeValidationReason

CompanyId = Annotated[str, StringConstraints(strict=True, min_length=1, max_length=200)]
ActorId = Annotated[str, StringConstraints(strict=True, min_length=1, max_length=200)]
ContentHash = Annotated[str, StringConstraints(strict=True, pattern=r"^[0-9a-f]{64}$")]


class KnowledgeCategory(StrEnum):
    """A small fixed vocabulary: classification only (never provider-specific)."""

    SOP = "sop"
    POLICY = "policy"
    PRICING = "pricing"
    RETURNS = "returns"
    SHIPPING = "shipping"
    SUPPLIER = "supplier"
    GENERAL = "general"


class DocumentLifecycle(StrEnum):
    ACTIVE = "active"
    ARCHIVED = "archived"  # excluded from retrieval; history stays inspectable


class ContentType(StrEnum):
    """The only accepted content types: text, never parsed or rendered as HTML."""

    TEXT_PLAIN = "text/plain"
    TEXT_MARKDOWN = "text/markdown"


class KnowledgeDocument(BaseModel):
    model_config = _FROZEN

    document_id: UUID
    company_id: CompanyId
    category: KnowledgeCategory
    lifecycle: DocumentLifecycle
    current_version: int = Field(ge=1)
    created_at: AwareDatetime
    updated_at: AwareDatetime


class KnowledgeDocumentVersion(BaseModel):
    """One immutable content snapshot."""

    model_config = _FROZEN

    document_id: UUID
    company_id: CompanyId
    version: int = Field(ge=1)
    title: Annotated[str, StringConstraints(strict=True, min_length=1, max_length=MAX_TITLE_CHARS)]
    content_type: ContentType
    body: Annotated[str, StringConstraints(strict=True, min_length=1, max_length=MAX_BODY_CHARS)]
    content_hash: ContentHash
    created_by_actor_id: ActorId
    created_at: AwareDatetime


class DocumentContent(BaseModel):
    """A validated, normalized document version to publish (no identity, no version)."""

    model_config = _FROZEN

    title: str
    content_type: ContentType
    body: str

    @property
    def content_hash(self) -> str:
        return content_hash(self.title, self.content_type, self.body)


def content_hash(title: str, content_type: ContentType | str, body: str) -> str:
    """SHA-256 of the canonical version content: integrity and duplicate detection only,
    never authorization or proof of trust."""
    canonical = json.dumps([title, str(content_type), body], ensure_ascii=False,
                           separators=(",", ":"))  # fmt: skip
    return hashlib.sha256(canonical.encode()).hexdigest()


def _no_control_characters(text: str, *, allow_newlines: bool) -> bool:
    allowed = {"\n", "\t"} if allow_newlines else set()
    return not any(unicodedata.category(ch) == "Cc" and ch not in allowed for ch in text)


def validate_title(title: object) -> str:
    if not isinstance(title, str):
        raise KnowledgeValidationError(R.TITLE_INVALID)
    title = " ".join(title.split())  # one line, single spaces
    if not 1 <= len(title) <= MAX_TITLE_CHARS or not _no_control_characters(
        title, allow_newlines=False
    ):
        raise KnowledgeValidationError(R.TITLE_INVALID)
    return title


def validate_body(body: object) -> str:
    """Normalizes line endings to ``\\n``; refuses blank, oversized or control-character
    content (including NUL, which PostgreSQL text cannot hold)."""
    if not isinstance(body, str):
        raise KnowledgeValidationError(R.BODY_INVALID)
    body = body.replace("\r\n", "\n").replace("\r", "\n")
    if len(body) > MAX_BODY_CHARS or len(body.encode()) > MAX_BODY_BYTES:
        raise KnowledgeValidationError(R.BODY_TOO_LARGE)
    if not body.strip() or not _no_control_characters(body, allow_newlines=True):
        raise KnowledgeValidationError(R.BODY_INVALID)
    return body


def validate_content_type(content_type: object) -> ContentType:
    try:
        return ContentType(content_type)  # type: ignore[arg-type]
    except ValueError:
        raise KnowledgeValidationError(R.CONTENT_TYPE_UNSUPPORTED) from None


def validate_category(category: object) -> KnowledgeCategory:
    try:
        return KnowledgeCategory(category)  # type: ignore[arg-type]
    except ValueError:
        raise KnowledgeValidationError(R.CATEGORY_UNSUPPORTED) from None


def validate_content(title: object, content_type: object, body: object) -> DocumentContent:
    return DocumentContent(title=validate_title(title),
                           content_type=validate_content_type(content_type),
                           body=validate_body(body))  # fmt: skip
