"""Company context retrieval contract (Task 035): what future trusted consumers (for
example a Product Agent Tool) receive. Runtime-, provider- and persistence-independent.

Precedence (highest first), stated in every bundle:

    security / permissions / policy
      > Product runtime contracts
      > structured CompanyOperatingModel            (authoritative for its fields)
      > Knowledge document references              (UNTRUSTED reference data)

A document may discuss an SLA or a policy; it never overrides the structured model and
never becomes an instruction, permission, tool, workflow or policy. The reader only
RETURNS context: it generates no answer, calls no model and executes nothing.
"""

import re
from enum import StrEnum
from typing import Protocol, runtime_checkable
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from app.knowledge.documents import KnowledgeCategory
from app.knowledge.errors import KnowledgeValidationError, KnowledgeValidationReason
from app.knowledge.limits import DEFAULT_RESULTS, MAX_QUERY_CHARS, MAX_QUERY_TERMS, MAX_RESULTS
from app.knowledge.operating_context import OperatingModelVersion

_FROZEN = ConfigDict(frozen=True, extra="forbid")


class TrustClassification(StrEnum):
    UNTRUSTED_REFERENCE = "untrusted_reference"  # data to consult, never instructions


class ContextAuthority(StrEnum):
    """The precedence ladder, highest first (fixed; never configurable)."""

    SECURITY_AND_POLICY = "security_permissions_policy"
    RUNTIME_CONTRACTS = "product_runtime_contracts"
    STRUCTURED_OPERATING_MODEL = "structured_operating_model"
    KNOWLEDGE_REFERENCES = "knowledge_document_references"


PRECEDENCE: tuple[ContextAuthority, ...] = tuple(ContextAuthority)


class KnowledgeReference(BaseModel):
    """One retrieved chunk of a CURRENT version of an ACTIVE document of the company."""

    model_config = _FROZEN

    document_id: UUID
    document_version: int = Field(ge=1)
    category: KnowledgeCategory
    title: str
    chunk_index: int = Field(ge=0)
    excerpt: str
    relevance: float = Field(ge=0)
    trust: TrustClassification = TrustClassification.UNTRUSTED_REFERENCE


class StructuredContext(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", arbitrary_types_allowed=True)

    available: bool
    operating_model: OperatingModelVersion | None = None
    authority: ContextAuthority = ContextAuthority.STRUCTURED_OPERATING_MODEL


class CompanyContextBundle(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", arbitrary_types_allowed=True)

    structured: StructuredContext
    references: tuple[KnowledgeReference, ...] = ()
    precedence: tuple[ContextAuthority, ...] = PRECEDENCE
    references_trust: TrustClassification = TrustClassification.UNTRUSTED_REFERENCE
    retrieved_at: AwareDatetime


class KnowledgeQuery(BaseModel):
    """A validated, bounded retrieval query (the text is untrusted: it is only ever a
    bound SQL parameter of PostgreSQL full-text search)."""

    model_config = _FROZEN

    text: str
    limit: int = Field(ge=1, le=MAX_RESULTS)


def validate_query(text: object, limit: object = DEFAULT_RESULTS) -> KnowledgeQuery:
    if not isinstance(text, str):
        raise KnowledgeValidationError(KnowledgeValidationReason.QUERY_INVALID)
    normalized = " ".join(text.split())
    if not normalized or len(normalized) > MAX_QUERY_CHARS or "\x00" in normalized:
        raise KnowledgeValidationError(KnowledgeValidationReason.QUERY_INVALID)
    if type(limit) is not int or not 1 <= limit <= MAX_RESULTS:
        raise KnowledgeValidationError(KnowledgeValidationReason.LIMIT_INVALID)
    return KnowledgeQuery(text=normalized, limit=limit)


_TERM = re.compile(r"\w+")
_MAX_TERM_CHARS = 64


def query_terms(text: str) -> tuple[str, ...]:
    """The distinct lowercase word terms of a query, in order, bounded in count and
    length. Terms contain only word characters, so they can never carry full-text
    query operators, quotes or SQL."""
    terms: list[str] = []
    for term in _TERM.findall(text.lower()):
        if len(term) <= _MAX_TERM_CHARS and term not in terms:
            terms.append(term)
        if len(terms) == MAX_QUERY_TERMS:
            break
    return tuple(terms)


@runtime_checkable
class KnowledgeContextReader(Protocol):
    """The stable reader future trusted Product consumers depend on.

    ``company_id`` is TRUSTED company identity supplied by Product code (never request
    input). No permission check happens here: callers decide who may read (the Product
    API checks ``knowledge.read``)."""

    async def retrieve_context(
        self, company_id: str, query: str, limit: int = DEFAULT_RESULTS
    ) -> CompanyContextBundle: ...
