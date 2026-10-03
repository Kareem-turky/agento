"""Product Knowledge composition (Task 035), independent of the business backend.

    settings -> (database configured?)  no  -> no service (Knowledge routes answer 503)
             -> installed Agent ids (ProductAgentCatalog; built before any resource)
             -> engine + sessions -> PostgresKnowledgeRepository
             -> RepositoryKnowledgeContextReader (bounded, company-scoped retrieval)
             -> GovernanceGate(KNOWLEDGE_ACTIONS, KnowledgePermissionEvaluator)
             -> handlers + ExecutionCoordinator (+ PostgresAuditSink: the existing audit)
             -> KnowledgeService (observed through the ONE Product observability given
                by the composition root; never built here)

No Agent consumes Knowledge in this build: nothing here registers a tool, injects
context into a prompt, builds an Agent or calls a model, an embedding provider or the
network. Nothing here migrates or creates tables.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.composition.contracts import DeploymentCompositionError
from app.config import Settings
from app.observability.contracts import ProductObservability

if TYPE_CHECKING:
    from app.knowledge.service import KnowledgeService

RELEASE_FAILED = "knowledge resources could not be released"


async def _nothing() -> None:
    return None


@dataclass(frozen=True)
class KnowledgeComposition:
    service: "KnowledgeService | None" = None
    close: Callable[[], Awaitable[None]] = _nothing
    discard: Callable[[], None] = lambda: None


def build_knowledge(
    settings: Settings, *, observability: ProductObservability | None = None
) -> KnowledgeComposition:
    if settings.database_url is None:
        return KnowledgeComposition()
    # Imported only here (called after the business backend was allowed).
    from app.agent_management.catalog import build_default_agent_catalog
    from app.execution import ActionHandlerRegistry, ExecutionCoordinator
    from app.governance import ActionCatalog, GovernanceGate
    from app.knowledge.actions import KNOWLEDGE_ACTIONS
    from app.knowledge.handlers import build_knowledge_handlers
    from app.knowledge.permissions import KnowledgePermissionEvaluator
    from app.knowledge.reader import RepositoryKnowledgeContextReader
    from app.knowledge.service import KnowledgeService
    from app.persistence import (
        PostgresAuditSink,
        PostgresKnowledgeRepository,
        create_product_engine,
        create_session_factory,
    )

    installed = build_default_agent_catalog().agent_ids  # before any resource is acquired
    engine = create_product_engine(str(settings.database_url))
    released = False

    async def close() -> None:
        nonlocal released
        if released:
            return
        released = True
        try:
            await engine.dispose()
        except Exception:  # noqa: BLE001 - never surface driver/URL details
            raise DeploymentCompositionError(RELEASE_FAILED) from None

    def discard() -> None:
        nonlocal released
        if released:
            return
        released = True
        try:
            engine.sync_engine.dispose()
        except Exception:  # noqa: BLE001 - never surface driver/URL details
            raise DeploymentCompositionError(RELEASE_FAILED) from None

    try:
        sessions = create_session_factory(engine)
        repository = PostgresKnowledgeRepository(sessions)
        reader = RepositoryKnowledgeContextReader(repository, repository)
        gate = GovernanceGate(ActionCatalog(KNOWLEDGE_ACTIONS),
                              permissions=KnowledgePermissionEvaluator())  # fmt: skip
        handlers = ActionHandlerRegistry(build_knowledge_handlers(repository, repository,
                                                                  installed))  # fmt: skip
        coordinator = ExecutionCoordinator(gate, handlers, PostgresAuditSink(sessions))
        service = KnowledgeService(repository, repository, reader, gate, coordinator,
                                   installed_agents=installed,
                                   observability=observability)  # fmt: skip
        return KnowledgeComposition(service=service, close=close, discard=discard)
    except BaseException:
        discard()
        raise
