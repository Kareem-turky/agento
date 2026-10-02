"""Agent-management composition (Task 032), independent of the business backend.

    settings -> (database configured?)  no  -> no service (Agent routes answer 503;
                                               Agents keep their definition defaults)
             -> engine + sessions -> PostgresAgentConfigurationRepository
             -> ProductCapabilityGraph: the Agent, Skill and Task catalogs, validated
                against each other (fails closed: an inconsistent build does not start)
             -> GovernanceGate(AGENT_MANAGEMENT_ACTIONS, HumanOperatorPermissionEvaluator)
             -> handlers + ExecutionCoordinator (+ PostgresAuditSink: the existing audit)
             -> AgentManagementService

Nothing here migrates or creates tables, builds or imports an Agent, or reads stored
values into code paths.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.composition.contracts import DeploymentCompositionError
from app.config import Settings

if TYPE_CHECKING:
    from app.agent_management.service import AgentManagementService

RELEASE_FAILED = "agent management resources could not be released"


async def _nothing() -> None:
    return None


@dataclass(frozen=True)
class AgentManagementComposition:
    service: "AgentManagementService | None" = None
    close: Callable[[], Awaitable[None]] = _nothing
    discard: Callable[[], None] = lambda: None


def build_agent_management(settings: Settings) -> AgentManagementComposition:
    if settings.database_url is None:
        return AgentManagementComposition()
    # Imported only here (called after the business backend was allowed): building a
    # refused deployment never loads persistence or Agent management.
    from app.agent_management.actions import AGENT_MANAGEMENT_ACTIONS
    from app.agent_management.capabilities import build_default_capability_graph
    from app.agent_management.handlers import build_agent_management_handlers
    from app.agent_management.permissions import HumanOperatorPermissionEvaluator
    from app.agent_management.service import AgentManagementService
    from app.execution import ActionHandlerRegistry, ExecutionCoordinator
    from app.governance import ActionCatalog, GovernanceGate
    from app.persistence import (
        PostgresAgentConfigurationRepository,
        PostgresAuditSink,
        create_product_engine,
        create_session_factory,
    )

    capabilities = build_default_capability_graph()  # before any resource is acquired
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
        repository = PostgresAgentConfigurationRepository(sessions)
        catalog = capabilities.agents
        gate = GovernanceGate(ActionCatalog(AGENT_MANAGEMENT_ACTIONS),
                              permissions=HumanOperatorPermissionEvaluator())  # fmt: skip
        handlers = ActionHandlerRegistry(build_agent_management_handlers(catalog, repository))
        coordinator = ExecutionCoordinator(gate, handlers, PostgresAuditSink(sessions))
        service = AgentManagementService(catalog, repository, gate, coordinator,
                                         capabilities=capabilities)  # fmt: skip
        return AgentManagementComposition(service=service, close=close, discard=discard)
    except BaseException:
        discard()
        raise
