"""Real product stack for operations tests. Only the audit sink is a test double."""

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from app.context.models import ActorContext, RequestContext
from app.execution import ActionHandlerRegistry, ActionRun, ExecutionCoordinator
from app.governance import ActionCatalog, ActionIntent, ActionScope, GovernanceGate
from app.integrations.commerce import TicketingIntegration
from app.integrations.commerce.mock import (
    EntityType,
    MockCommerceSystem,
    MockTicketDesk,
    MockTicketingAdapter,
    MockTicketWriteMode,
    canonical_id,
)
from app.operations import OPERATIONS_ACTIONS, CreateOperationalTicketHandler
from tests.execution.fakes import FIXED_TIME, RecordingAuditSink, run

ACTION = "operations.ticket.create"
COMPANY = str(canonical_id(EntityType.COMPANY, "acct_demo"))
STORE = str(canonical_id(EntityType.STORE, "shop_north"))
OTHER_STORE = str(canonical_id(EntityType.STORE, "shop_south"))
OTHER_COMPANY = str(UUID("0b0b0b0b-0000-4000-8000-000000000001"))
PARAMS = {"title": "Parcel delayed", "description": "Courier has not scanned the parcel."}


class SpyTicketing:
    """Delegates to the real mock adapter and records every call (for assertions)."""

    def __init__(self, inner: TicketingIntegration) -> None:
        self.inner = inner
        self.creates: list[dict[str, Any]] = []
        self.correlation_reads: list[UUID] = []

    async def create_ticket(self, **kwargs: Any):
        self.creates.append(kwargs)
        return await self.inner.create_ticket(**kwargs)

    async def get_ticket(self, ticket_id: UUID):
        return await self.inner.get_ticket(ticket_id)

    async def find_ticket_by_correlation(self, correlation_id: UUID):
        self.correlation_reads.append(correlation_id)
        return await self.inner.find_ticket_by_correlation(correlation_id)


@dataclass
class Stack:
    desk: MockTicketDesk
    adapter: MockTicketingAdapter
    spy: SpyTicketing
    handler: CreateOperationalTicketHandler
    sink: RecordingAuditSink
    coordinator: ExecutionCoordinator

    def run(
        self,
        params: dict[str, Any] | None = None,
        *,
        req: RequestContext | None = None,
        scope: ActionScope | None = None,
        action: str = ACTION,
    ) -> ActionRun:
        return run(
            self.coordinator.run(
                req or request(),
                ActionIntent(name=action),
                scope or ActionScope(company_id=COMPANY, store_id=STORE),
                PARAMS if params is None else params,
            )
        )


def stack(
    mode: MockTicketWriteMode = MockTicketWriteMode.NORMAL,
    *,
    sink: RecordingAuditSink | None = None,
    tickets: TicketingIntegration | None = None,
) -> Stack:
    desk = MockTicketDesk(mode=mode)
    adapter = MockTicketingAdapter(MockCommerceSystem(), desk)
    spy = SpyTicketing(tickets or adapter)
    handler = CreateOperationalTicketHandler(spy)
    sink = sink or RecordingAuditSink()
    coordinator = ExecutionCoordinator(
        GovernanceGate(ActionCatalog(OPERATIONS_ACTIONS)),
        ActionHandlerRegistry([handler]),
        sink,
        clock=lambda: FIXED_TIME,
    )
    return Stack(desk, adapter, spy, handler, sink, coordinator)


def actor(**overrides: Any) -> ActorContext:
    data: dict[str, Any] = {
        "actor_id": "ops-user-1",
        "actor_type": "user",
        "company_id": COMPANY,
        "permissions": frozenset({"tickets.create"}),
        "store_ids": frozenset({STORE}),
    }
    data.update(overrides)
    return ActorContext(**data)


def request(actor_ctx: ActorContext | None = None, **kw: Any) -> RequestContext:
    return RequestContext(actor=actor_ctx if actor_ctx is not None else actor(), **kw)
