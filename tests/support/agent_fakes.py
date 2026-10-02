"""TEST-ONLY Agent-management helpers: an in-memory configuration repository, a
recording Operations run service and a builder for the real service. Never used by
production code."""

from datetime import datetime

from app.agent_management import (
    AgentConfiguration,
    AgentConfigurationRepositoryError,
    ProductAgentCatalog,
    build_default_agent_catalog,
)
from app.agent_management.actions import AGENT_MANAGEMENT_ACTIONS
from app.agent_management.handlers import build_agent_management_handlers
from app.agent_management.permissions import HumanOperatorPermissionEvaluator
from app.agent_management.service import AgentManagementService
from app.context.models import RequestContext
from app.execution import ActionHandlerRegistry, ExecutionCoordinator
from app.governance import ActionCatalog, ActionScope, GovernanceGate
from app.services.operations import ProductOperationsRunResult
from tests.support.integration_fakes import RecordingAuditSink, StepClock


class InMemoryAgentConfigurationRepository:
    def __init__(self) -> None:
        self.rows: dict[tuple[str, str], AgentConfiguration] = {}
        self.fail = False

    def _check(self) -> None:
        if self.fail:
            raise AgentConfigurationRepositoryError()

    async def get(self, company_id: str, agent_id: str) -> AgentConfiguration | None:
        self._check()
        return self.rows.get((company_id, agent_id))

    async def list(self, company_id: str) -> tuple[AgentConfiguration, ...]:
        self._check()
        return tuple(v for (c, _), v in sorted(self.rows.items()) if c == company_id)

    async def set_enabled(
        self, company_id: str, agent_id: str, enabled: bool, at: datetime
    ) -> AgentConfiguration:
        self._check()
        existing = self.rows.get((company_id, agent_id))
        row = AgentConfiguration(
            company_id=company_id, agent_id=agent_id, enabled=enabled,
            created_at=existing.created_at if existing else at, updated_at=at,
        )  # fmt: skip
        self.rows[(company_id, agent_id)] = row
        return row

    async def delete(self, company_id: str, agent_id: str) -> bool:
        self._check()
        return self.rows.pop((company_id, agent_id), None) is not None


class RecordingOperationsService:
    """Stands in for the Operations runner: records every call it actually receives."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def run_product(
        self, request: RequestContext, scope: ActionScope, message: str
    ) -> ProductOperationsRunResult:
        self.calls.append((scope.company_id, message))
        return ProductOperationsRunResult(message="recorded run")


def build_agent_service(
    catalog: ProductAgentCatalog | None = None,
) -> tuple[AgentManagementService, InMemoryAgentConfigurationRepository, RecordingAuditSink]:
    installed = catalog if catalog is not None else build_default_agent_catalog()
    repository = InMemoryAgentConfigurationRepository()
    audit = RecordingAuditSink()
    gate = GovernanceGate(ActionCatalog(AGENT_MANAGEMENT_ACTIONS),
                          permissions=HumanOperatorPermissionEvaluator())  # fmt: skip
    handlers = ActionHandlerRegistry(
        build_agent_management_handlers(installed, repository, clock=StepClock())
    )
    coordinator = ExecutionCoordinator(gate, handlers, audit)
    return AgentManagementService(installed, repository, gate, coordinator), repository, audit
