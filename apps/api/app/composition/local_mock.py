"""LOCAL/TEST ONLY: the real Product core wired to the deterministic mock backend.

    one Product AsyncEngine -> one async_sessionmaker
      -> PostgresWriteCommandStore (WriteCommandStore + WriteCommandReader)
      -> PostgresAuditSink
    ActionCatalog(OPERATIONS_ACTIONS) -> GovernanceGate
    ONE MockCommerceSystem -> MockCommerceAdapter                       (governed reads)
                           -> MockTicketingAdapter (+ MockTicketDesk)   (ticket writes)
    CreateOperationalTicketHandler -> ActionHandlerRegistry
      -> ExecutionCoordinator(gate, registry, PostgresAuditSink,
                              approvals=ProductApprovalBroker(PostgresApprovalRepository))
         (Task 036: a MEDIUM/HIGH action would request a human decision; the mock
          backend's only write, the ticket, is LOW_RISK_WRITE and never does)
      -> WriteCommandCoordinator(store, coordinator, catalog)
      -> WriteCommandTicketService / WriteCommandTicketQueryService(store)
    DailyOperationsWorkflow(commerce=<the SAME MockCommerceAdapter>, gate=<the SAME gate>)
      -> WorkflowRuntimeRegistry(catalog, [daily_report_registration(<that workflow>)])
      -> WorkflowEngine(catalog, registry, PostgresWorkflowRunRepository(sessions),
                        observability=<the application's ONE Product observability>)
      -> WorkflowBackedDailyOperationsReportService(engine)
      -> the HTTP report service AND the agent's report tool (one instance): every report
         is a durable ``operations.daily_report`` Workflow run (Task 034)
    build_operations_agent(model, commerce, gate, coordinator, daily_operations)
      -> OperationsAgentRunner

The read and write adapters share one mock system, so they resolve the same canonical
company and stores. Everything except the mock provider is the Product core a real
backend will use. The mock ticket desk is in memory: its tickets do not survive a
restart (Product-owned command and audit state in PostgreSQL does).

This is the only application module that imports the mock integration. It refuses to
build outside local/test, never migrates, and releases the engine it created if
composition fails part-way.
"""

from agno.models.base import Model
from sqlalchemy.ext.asyncio import AsyncEngine

from app.agents.operations import OperationsAgentRunner, build_operations_agent
from app.application.operations_ticket_queries import WriteCommandTicketQueryService
from app.application.operations_tickets import WriteCommandTicketService
from app.approval_management.broker import ProductApprovalBroker
from app.commands import WriteCommandCoordinator
from app.composition.contracts import (
    BACKEND_NOT_ALLOWED,
    DATABASE_REQUIRED,
    MODEL_REQUIRED,
    RELEASE_FAILED,
    DeploymentComposition,
    DeploymentCompositionError,
)
from app.config import DEVELOPMENT_ENVIRONMENTS, Settings
from app.execution import ActionHandlerRegistry, ExecutionCoordinator
from app.governance import ActionCatalog, GovernanceGate
from app.integrations.commerce.mock import (
    MockCommerceAdapter,
    MockCommerceSystem,
    MockTicketDesk,
    MockTicketingAdapter,
)
from app.observability.contracts import ProductObservability
from app.operations import OPERATIONS_ACTIONS, CreateOperationalTicketHandler
from app.persistence import (
    PostgresApprovalRepository,
    PostgresAuditSink,
    PostgresWorkflowRunRepository,
    PostgresWriteCommandStore,
    create_product_engine,
    create_session_factory,
)
from app.runtime.models import build_default_model
from app.workflow_management.catalog import build_default_workflow_catalog
from app.workflow_management.engine import WorkflowEngine
from app.workflow_management.handlers import WorkflowRuntimeRegistry
from app.workflows import DailyOperationsWorkflow
from app.workflows.operations_daily_platform import (
    WorkflowBackedDailyOperationsReportService,
    daily_report_registration,
)


class _EngineLifecycle:
    """Releases the composition-owned engine exactly once."""

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine
        self._released = False

    async def close(self) -> None:
        if self._released:
            return
        self._released = True
        try:
            await self._engine.dispose()
        except Exception:  # noqa: BLE001 - never surface driver/URL details
            raise DeploymentCompositionError(RELEASE_FAILED) from None

    def discard(self) -> None:
        """Synchronous release for a composition that never became an application."""
        if self._released:
            return
        self._released = True
        try:
            self._engine.sync_engine.dispose()
        except Exception:  # noqa: BLE001 - never surface driver/URL details
            raise DeploymentCompositionError(RELEASE_FAILED) from None


def build_local_mock_composition(
    settings: Settings,
    *,
    model: Model | None = None,
    observability: ProductObservability | None = None,
) -> DeploymentComposition:
    # Defense in depth: the registry already refused other environments.
    if settings.environment not in DEVELOPMENT_ENVIRONMENTS:
        raise DeploymentCompositionError(BACKEND_NOT_ALLOWED)
    operations_model = model if model is not None else build_default_model(settings)
    if operations_model is None:
        # A "complete" mock runtime must not expose /operations/runs as a permanent 503.
        raise DeploymentCompositionError(MODEL_REQUIRED)
    if settings.database_url is None:
        raise DeploymentCompositionError(DATABASE_REQUIRED)
    workflows = build_default_workflow_catalog()  # static; validated before any resource

    engine = create_product_engine(str(settings.database_url))
    lifecycle = _EngineLifecycle(engine)
    try:
        sessions = create_session_factory(engine)
        store = PostgresWriteCommandStore(sessions)
        audit = PostgresAuditSink(sessions)

        catalog = ActionCatalog(OPERATIONS_ACTIONS)
        gate = GovernanceGate(catalog)

        system = MockCommerceSystem()  # one provider system for reads and writes
        commerce = MockCommerceAdapter(system)
        ticketing = MockTicketingAdapter(system, MockTicketDesk())

        registry = ActionHandlerRegistry([CreateOperationalTicketHandler(ticketing)])
        # Durable human approvals, observed through the SAME Product observability.
        approvals = ProductApprovalBroker(PostgresApprovalRepository(sessions),
                                          observability=observability)  # fmt: skip
        coordinator = ExecutionCoordinator(gate, registry, audit, approvals=approvals)
        commands = WriteCommandCoordinator(store, coordinator, catalog)

        # ONE deterministic report service, shared by the HTTP report route and the
        # Operations Agent's report tool (same adapter, same gate), executed as the
        # durable ``operations.daily_report`` Product Workflow. The existing workflow
        # still computes every report; the platform only orchestrates it.
        report = DailyOperationsWorkflow(commerce=commerce, gate=gate)
        bindings = WorkflowRuntimeRegistry(workflows, [daily_report_registration(report)])
        # Workflow runs are observed through the SAME Product observability the
        # application uses (never a second default instance).
        platform = WorkflowEngine(workflows, bindings, PostgresWorkflowRunRepository(sessions),
                                  observability=observability)  # fmt: skip
        daily_operations = WorkflowBackedDailyOperationsReportService(platform)
        agent = build_operations_agent(
            operations_model, commerce=commerce, gate=gate, coordinator=coordinator,
            daily_operations=daily_operations,
        )  # fmt: skip
        return DeploymentComposition(
            default_model=operations_model,
            operations_service=OperationsAgentRunner(agent),
            operations_ticket_service=WriteCommandTicketService(commands),
            operations_ticket_query_service=WriteCommandTicketQueryService(store),
            daily_operations_service=daily_operations,
            approval_workflow_resumer=platform,
            close=lifecycle.close,
            discard=lifecycle.discard,
        )
    except BaseException:
        lifecycle.discard()
        raise
