"""Real Operations Agent stack for tests. Only the model and the audit sink are
test doubles."""

import asyncio
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from agno.agent import Agent
from agno.run.agent import RunOutput

from app.agents.operations import OperationsAgentRunner, build_operations_agent
from app.commerce.domain import Order, Shipment
from app.context.models import ActorContext, RequestContext
from app.execution import ActionHandlerRegistry, ExecutionCoordinator  # noqa: F401
from app.governance import ActionCatalog, ActionScope, GovernanceGate
from app.integrations.commerce import CommerceIntegration, ShipmentQuery
from app.integrations.commerce.mock import (
    EntityType,
    MockCommerceAdapter,
    MockCommerceSystem,
    MockTicketDesk,
    MockTicketingAdapter,
    MockTicketWriteMode,
    canonical_id,
)
from app.operations import (
    CREATE_TICKET_ACTION,
    OPERATIONS_ACTIONS,
    CreateOperationalTicketHandler,
)
from app.workflows import DailyOperationsWorkflow
from tests.execution.fakes import FIXED_TIME, RecordingAuditSink
from tests.support.scripted_tool_model import ScriptedToolModel, Step

COMPANY = str(canonical_id(EntityType.COMPANY, "acct_demo"))
STORE = str(canonical_id(EntityType.STORE, "shop_south"))
OTHER_STORE = str(canonical_id(EntityType.STORE, "shop_north"))
ORDER = str(canonical_id(EntityType.ORDER, "ord_2002"))  # has a failed + a moving shipment
OTHER_STORE_ORDER = str(canonical_id(EntityType.ORDER, "ord_1003"))
ACTOR_ID = "ops-user-7f3a"
ROLE_ID = "ops-role-91c2"
ALL_PERMISSIONS = frozenset({"orders.read", "shipments.read", "tickets.create"})
TICKET_WRITE = frozenset({CREATE_TICKET_ACTION.name})  # trusted per-run write intent


class SpyCoordinator(ExecutionCoordinator):
    """The real coordinator, counting how often a tool entered it."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.runs = 0

    async def run(self, *args: Any, **kwargs: Any):
        self.runs += 1
        return await super().run(*args, **kwargs)


class SpyCommerce:
    """Delegates to a real commerce adapter and counts the reads."""

    def __init__(self, inner: MockCommerceAdapter) -> None:
        self.inner = inner
        self.get_order_calls: list[UUID] = []
        self.list_shipments_calls: list[ShipmentQuery | None] = []

    @property
    def descriptor(self):
        return self.inner.descriptor

    async def get_store(self, store_id):
        return await self.inner.get_store(store_id)

    async def get_order(self, order_id: UUID) -> Order:
        self.get_order_calls.append(order_id)
        return await self.inner.get_order(order_id)

    async def list_orders(self, query=None):
        return await self.inner.list_orders(query)

    async def get_shipment(self, shipment_id):
        return await self.inner.get_shipment(shipment_id)

    async def list_shipments(self, query=None) -> tuple[Shipment, ...]:
        self.list_shipments_calls.append(query)
        return await self.inner.list_shipments(query)

    async def get_inventory(self, variant_id, warehouse_id=None):
        return await self.inner.get_inventory(variant_id, warehouse_id)


@dataclass
class OpsStack:
    model: ScriptedToolModel
    agent: Agent
    runner: OperationsAgentRunner
    commerce: SpyCommerce
    desk: MockTicketDesk
    ticketing: MockTicketingAdapter
    sink: RecordingAuditSink
    coordinator: SpyCoordinator
    outputs: list[RunOutput] = field(default_factory=list)

    def run(
        self,
        message: str,
        *,
        req: RequestContext | None = None,
        scope: ActionScope | None = None,
        writes: frozenset[str] = frozenset(),
    ) -> RunOutput:
        output = asyncio.run(
            self.runner.run(
                req or request(),
                scope or ActionScope(company_id=COMPANY, store_id=STORE),
                message,
                requested_write_actions=writes,
            )
        )
        self.outputs.append(output)
        return output

    def run_raw(self, message: str, **kwargs: Any) -> RunOutput:
        """Call Agent.arun directly (bypassing the runner) with arbitrary kwargs."""
        return asyncio.run(self.agent.arun(message, **kwargs))


def ops_stack(
    script: list[Step],
    *,
    mode: MockTicketWriteMode = MockTicketWriteMode.NORMAL,
    commerce: MockCommerceAdapter | None = None,
    sink: RecordingAuditSink | None = None,
    daily: Any = None,
) -> OpsStack:
    model = ScriptedToolModel(script=list(script))
    spy = SpyCommerce(commerce or MockCommerceAdapter())
    desk = MockTicketDesk(mode=mode)
    ticketing = MockTicketingAdapter(MockCommerceSystem(), desk)
    sink = sink or RecordingAuditSink()
    gate = GovernanceGate(ActionCatalog(OPERATIONS_ACTIONS))
    coordinator = SpyCoordinator(
        gate,
        ActionHandlerRegistry([CreateOperationalTicketHandler(ticketing)]),
        sink,
        clock=lambda: FIXED_TIME,
    )
    commerce_contract: CommerceIntegration = spy  # structural: the tools see the contract
    if daily is None:
        # The real deterministic workflow over the same (spied) commerce and gate.
        daily = DailyOperationsWorkflow(commerce=commerce_contract, gate=gate,
                                        clock=lambda: FIXED_TIME)  # fmt: skip
    agent = build_operations_agent(
        model, commerce=commerce_contract, gate=gate, coordinator=coordinator,
        daily_operations=daily,
    )  # fmt: skip
    return OpsStack(
        model, agent, OperationsAgentRunner(agent), spy, desk, ticketing, sink, coordinator
    )


def actor(**overrides: Any) -> ActorContext:
    data: dict[str, Any] = {
        "actor_id": ACTOR_ID,
        "actor_type": "user",
        "company_id": COMPANY,
        "role_ids": frozenset({ROLE_ID}),
        "permissions": ALL_PERMISSIONS,
        "store_ids": frozenset({STORE}),
    }
    data.update(overrides)
    return ActorContext(**data)


def request(actor_ctx: ActorContext | None = None, **kw: Any) -> RequestContext:
    return RequestContext(actor=actor_ctx if actor_ctx is not None else actor(), **kw)
