"""LOCAL/TEST ONLY: the real Product core wired to the deterministic mock backend.

    one Product AsyncEngine -> one async_sessionmaker
      -> PostgresWriteCommandStore (WriteCommandStore + WriteCommandReader)
      -> PostgresAuditSink
    ActionCatalog(OPERATIONS_ACTIONS) -> GovernanceGate
    ONE MockCommerceSystem -> MockCommerceAdapter                       (governed reads)
                           -> MockTicketingAdapter (+ MockTicketDesk)   (ticket writes)
    CreateOperationalTicketHandler -> ActionHandlerRegistry
      -> ExecutionCoordinator(gate, registry, PostgresAuditSink)
      -> WriteCommandCoordinator(store, coordinator, catalog)
      -> WriteCommandTicketService / WriteCommandTicketQueryService(store)
    build_operations_agent(model, commerce, gate, coordinator) -> OperationsAgentRunner

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
from app.commands import WriteCommandCoordinator
from app.composition.deployment import (
    DATABASE_REQUIRED,
    MOCK_NOT_ALLOWED,
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
from app.operations import OPERATIONS_ACTIONS, CreateOperationalTicketHandler
from app.persistence import (
    PostgresAuditSink,
    PostgresWriteCommandStore,
    create_product_engine,
    create_session_factory,
)
from app.runtime.models import build_default_model


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
    settings: Settings, *, model: Model | None = None
) -> DeploymentComposition:
    # Defense in depth: the deployment selector already refused other environments.
    if settings.environment not in DEVELOPMENT_ENVIRONMENTS:
        raise DeploymentCompositionError(MOCK_NOT_ALLOWED)
    operations_model = model if model is not None else build_default_model(settings)
    if operations_model is None:
        # A "complete" mock runtime must not expose /operations/runs as a permanent 503.
        raise DeploymentCompositionError(MODEL_REQUIRED)
    if settings.database_url is None:
        raise DeploymentCompositionError(DATABASE_REQUIRED)

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
        coordinator = ExecutionCoordinator(gate, registry, audit)
        commands = WriteCommandCoordinator(store, coordinator, catalog)

        agent = build_operations_agent(
            operations_model, commerce=commerce, gate=gate, coordinator=coordinator
        )
        return DeploymentComposition(
            default_model=operations_model,
            operations_service=OperationsAgentRunner(agent),
            operations_ticket_service=WriteCommandTicketService(commands),
            operations_ticket_query_service=WriteCommandTicketQueryService(store),
            close=lifecycle.close,
            discard=lifecycle.discard,
        )
    except BaseException:
        lifecycle.discard()
        raise
