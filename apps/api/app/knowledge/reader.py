"""``RepositoryKnowledgeContextReader``: the Product implementation of
``KnowledgeContextReader`` (Task 035).

It reads the company's current structured operating model and runs a bounded,
company-scoped lexical search; it RETURNS context only (no answer, no model, no
network, no execution). Results are bounded in count, per-excerpt length and total
characters; ordering is the repository's deterministic ranking.
"""

from collections.abc import Callable
from datetime import UTC, datetime

from app.knowledge.context import (
    CompanyContextBundle,
    KnowledgeReference,
    StructuredContext,
    validate_query,
)
from app.knowledge.contracts import KnowledgeDocumentRepository, OperatingModelRepository
from app.knowledge.limits import DEFAULT_RESULTS, MAX_EXCERPT_CHARS, MAX_TOTAL_RETURNED_CHARS


def _utc_now() -> datetime:
    return datetime.now(UTC)


class RepositoryKnowledgeContextReader:
    def __init__(
        self,
        operating_models: OperatingModelRepository,
        documents: KnowledgeDocumentRepository,
        *,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._operating_models = operating_models
        self._documents = documents
        self._clock = clock

    async def retrieve_context(
        self, company_id: str, query: str, limit: int = DEFAULT_RESULTS
    ) -> CompanyContextBundle:
        if not isinstance(company_id, str) or not company_id:
            raise ValueError("a trusted company id is required")
        bounded = validate_query(query, limit)
        model = await self._operating_models.current_operating_model(company_id)
        hits = await self._documents.search(company_id, bounded.text, bounded.limit)
        references: list[KnowledgeReference] = []
        budget = MAX_TOTAL_RETURNED_CHARS
        for hit in hits[: bounded.limit]:
            if budget <= 0:
                break
            excerpt = hit.content[: min(MAX_EXCERPT_CHARS, budget)]
            budget -= len(excerpt)
            references.append(KnowledgeReference(
                document_id=hit.document_id, document_version=hit.document_version,
                category=hit.category, title=hit.title, chunk_index=hit.chunk_index,
                excerpt=excerpt, relevance=max(hit.rank, 0.0),
            ))  # fmt: skip
        return CompanyContextBundle(
            structured=StructuredContext(available=model is not None, operating_model=model),
            references=tuple(references),
            retrieved_at=self._clock(),
        )
