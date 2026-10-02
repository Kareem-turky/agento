"""``AgentManagementService``: Product Agent lifecycle management (Task 032).

    reads      trusted RequestContext -> GovernanceGate (agents.read)   -> 403 / data
    mutations  trusted RequestContext -> GovernanceGate (agents.manage)
                 denied  -> ExecutionCoordinator (audits the denial)   -> 403
                 allowed -> installed agent? (404)
                         -> ExecutionCoordinator -> handler -> verify -> AUDIT
    runtime    ``runtime_state(company_id, agent_id)``: the trusted, permission-free
               lookup the Product run boundary uses to fail closed for a disabled Agent.

Identity and company come only from the trusted ``RequestContext`` (or, for the runtime
lookup, the trusted run scope). The gate uses ``HumanOperatorPermissionEvaluator``: an
Agent actor is never granted Agent management. Nothing here constructs, imports or runs
an Agent; stored configuration is only an enabled flag.
"""

from collections.abc import Mapping
from typing import Any

from app.agent_management import actions
from app.agent_management.capabilities import ProductCapabilityGraph
from app.agent_management.catalog import ProductAgentCatalog
from app.agent_management.configuration import (
    AgentConfiguration,
    AgentConfigurationRepository,
    AgentConfigurationRepositoryError,
)
from app.agent_management.definitions import AgentDefinition
from app.agent_management.skills import SkillDefinition, build_default_skill_catalog
from app.agent_management.state import AgentEffectiveState, effective_state
from app.agent_management.tasks import TaskDefinition, build_default_task_catalog
from app.context.models import ActorContext, RequestContext
from app.execution import ActionRunStatus, ExecutionCoordinator
from app.governance import (
    ActionDefinition,
    ActionIntent,
    ActionScope,
    GovernanceGate,
    PolicyOutcome,
)


class AgentManagementError(Exception):
    """Base class. Messages are fixed and never contain a submitted or stored value."""

    message = "agent management failed"

    def __init__(self) -> None:
        super().__init__(self.message)


class AgentAccessDeniedError(AgentManagementError):
    message = "Forbidden"


class AgentNotFoundError(AgentManagementError):
    message = "Agent not found"


class SkillNotFoundError(AgentManagementError):
    message = "Skill not found"


class TaskNotFoundError(AgentManagementError):
    message = "Task not found"


class AgentOperationFailedError(AgentManagementError):
    message = "Agent management operation did not complete"


class AgentManagementUnavailableError(AgentManagementError):
    message = "Agent management unavailable"


class AgentManagementService:
    def __init__(
        self,
        catalog: ProductAgentCatalog,
        repository: AgentConfigurationRepository,
        gate: GovernanceGate,
        coordinator: ExecutionCoordinator,
        *,
        capabilities: ProductCapabilityGraph | None = None,
        runtime_available: frozenset[str] = frozenset(),
    ) -> None:
        if capabilities is None:
            # Validated against this Agent catalog; fails closed when inconsistent.
            capabilities = ProductCapabilityGraph.build(
                catalog, build_default_skill_catalog(), build_default_task_catalog()
            )
        if capabilities.agents is not catalog:
            raise ValueError("the capability graph must describe this Agent catalog")
        self._capabilities = capabilities
        self._catalog = catalog
        self._repository = repository
        self._gate = gate
        self._coordinator = coordinator
        self._runtime_available = frozenset(runtime_available)

    def with_runtime(self, runtime_available: frozenset[str]) -> "AgentManagementService":
        """The same service, told which Agents' trusted runtimes were composed."""
        return AgentManagementService(
            self._catalog,
            self._repository,
            self._gate,
            self._coordinator,
            capabilities=self._capabilities,
            runtime_available=runtime_available,
        )

    # ----- authorization --------------------------------------------------------------------

    @staticmethod
    def _actor(context: RequestContext) -> ActorContext:
        if context.actor is None:
            raise AgentAccessDeniedError()
        return context.actor

    def _allowed(self, actor: ActorContext, action: ActionDefinition) -> bool:
        decision = self._gate.decide(
            actor, ActionIntent(name=action.name), ActionScope(company_id=actor.company_id)
        )
        return decision.outcome is PolicyOutcome.ALLOW

    def _authorize_read(self, context: RequestContext, action: ActionDefinition) -> ActorContext:
        actor = self._actor(context)
        if not self._allowed(actor, action):
            raise AgentAccessDeniedError()
        return actor

    async def _authorize_mutation(
        self, context: RequestContext, action: ActionDefinition
    ) -> ActorContext:
        actor = self._actor(context)
        if not self._allowed(actor, action):
            # The coordinator records the denial in the audit trail, then stops.
            await self._coordinator.run(
                context, ActionIntent(name=action.name),
                ActionScope(company_id=actor.company_id), {},
            )  # fmt: skip
            raise AgentAccessDeniedError()
        return actor

    async def _run(
        self,
        context: RequestContext,
        actor: ActorContext,
        action: ActionDefinition,
        parameters: Mapping[str, Any],
    ) -> None:
        run = await self._coordinator.run(
            context, ActionIntent(name=action.name),
            ActionScope(company_id=actor.company_id), parameters,
        )  # fmt: skip
        if run.status is ActionRunStatus.DENIED:
            raise AgentAccessDeniedError()
        if run.status is not ActionRunStatus.VERIFIED:
            raise AgentOperationFailedError()

    # ----- state ------------------------------------------------------------------------------

    def _definition(self, agent_id: str) -> AgentDefinition:
        definition = self._catalog.get(agent_id)
        if definition is None:
            raise AgentNotFoundError()
        return definition

    async def _override(self, company_id: str, agent_id: str) -> AgentConfiguration | None:
        try:
            return await self._repository.get(company_id, agent_id)
        except AgentConfigurationRepositoryError:
            raise AgentManagementUnavailableError() from None

    def _state(
        self, definition: AgentDefinition, override: AgentConfiguration | None
    ) -> AgentEffectiveState:
        return effective_state(
            definition, override, runtime_available=definition.agent_id in self._runtime_available
        )

    async def runtime_state(self, company_id: str, agent_id: str) -> AgentEffectiveState:
        """Trusted runtime lookup (no permission check: it only decides whether a Product
        run boundary may proceed). Fails closed: unknown agent or unreadable storage
        raises."""
        definition = self._definition(agent_id)
        return self._state(definition, await self._override(company_id, agent_id))

    # ----- reads ------------------------------------------------------------------------------

    def catalog(self, context: RequestContext) -> tuple[AgentDefinition, ...]:
        self._authorize_read(context, actions.CATALOG_READ)
        return self._catalog.definitions()

    async def list_agents(
        self, context: RequestContext
    ) -> tuple[tuple[AgentDefinition, AgentEffectiveState], ...]:
        actor = self._authorize_read(context, actions.CONFIGURATION_READ)
        try:
            stored = {c.agent_id: c for c in await self._repository.list(actor.company_id)}
        except AgentConfigurationRepositoryError:
            raise AgentManagementUnavailableError() from None
        # Rows for Agents not installed in this build are ignored (never surfaced).
        return tuple((d, self._state(d, stored.get(d.agent_id)))
                     for d in self._catalog.definitions())  # fmt: skip

    async def get_agent(
        self, context: RequestContext, agent_id: str
    ) -> tuple[AgentDefinition, AgentEffectiveState]:
        actor = self._authorize_read(context, actions.CONFIGURATION_READ)
        definition = self._definition(agent_id)
        return definition, self._state(definition, await self._override(actor.company_id, agent_id))

    # ----- Skills and Tasks (immutable Product metadata; read-only) ---------------------------

    @property
    def capabilities(self) -> ProductCapabilityGraph:
        return self._capabilities

    def skill_catalog(self, context: RequestContext) -> tuple[SkillDefinition, ...]:
        self._authorize_read(context, actions.SKILLS_READ)
        return self._capabilities.skills.definitions()

    def get_skill(self, context: RequestContext, skill_id: str) -> SkillDefinition:
        self._authorize_read(context, actions.SKILLS_READ)
        skill = self._capabilities.skills.get(skill_id)
        if skill is None:
            raise SkillNotFoundError()
        return skill

    def task_catalog(self, context: RequestContext) -> tuple[TaskDefinition, ...]:
        self._authorize_read(context, actions.TASKS_READ)
        return self._capabilities.tasks.definitions()

    def get_task(self, context: RequestContext, task_id: str) -> TaskDefinition:
        self._authorize_read(context, actions.TASKS_READ)
        task = self._capabilities.tasks.get(task_id)
        if task is None:
            raise TaskNotFoundError()
        return task

    # ----- mutations ----------------------------------------------------------------------------

    async def set_enabled(
        self, context: RequestContext, agent_id: str, enabled: bool
    ) -> tuple[AgentDefinition, AgentEffectiveState]:
        action = actions.AGENT_ENABLE if enabled else actions.AGENT_DISABLE
        actor = await self._authorize_mutation(context, action)
        definition = self._definition(agent_id)
        await self._run(context, actor, action, {"agent_id": agent_id, "enabled": enabled})
        return definition, self._state(definition, await self._override(actor.company_id, agent_id))

    async def reset_configuration(
        self, context: RequestContext, agent_id: str
    ) -> tuple[AgentDefinition, AgentEffectiveState]:
        actor = await self._authorize_mutation(context, actions.CONFIGURATION_RESET)
        definition = self._definition(agent_id)
        await self._run(context, actor, actions.CONFIGURATION_RESET, {"agent_id": agent_id})
        return definition, self._state(definition, await self._override(actor.company_id, agent_id))
