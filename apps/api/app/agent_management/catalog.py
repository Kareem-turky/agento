"""``ProductAgentCatalog``: the immutable, explicit list of Product business Agents
installed in this build.

There is no discovery: no entry points, directory scans, import paths, database or
remote code, and nothing can register an Agent over HTTP. The default catalog is a
reviewed static tuple. It holds exactly the Product business Agents; runtime
infrastructure Agents (the ``generic-reasoning`` model smoke Agent) are not Product
business Agents and are never listed here.
"""

from collections.abc import Iterable
from types import MappingProxyType

from app.agent_management.definitions import (
    AgentCategory,
    AgentDefinition,
    AgentLifecycle,
    AgentManifest,
    AgentSafetyProperty,
    AgentToolAccess,
    AgentToolDeclaration,
)

# The existing Product Operations Agent (app.agents.operations), described, not rebuilt.
# An architecture test keeps these facts equal to the trusted implementation.
OPERATIONS_AGENT_DEFINITION = AgentDefinition(
    agent_id="operations",
    name="Operations Agent",
    description="Analyzes orders, shipments and the deterministic daily operations report "
    "and can request governed operational tickets.",
    category=AgentCategory.OPERATIONS,
    lifecycle=AgentLifecycle.ACTIVE,
    default_enabled=True,
    capabilities=frozenset({"operations.analysis", "operations.daily_report.explain",
                            "operations.ticket.request"}),
    manifest=AgentManifest(
        tools=(
            AgentToolDeclaration(tool_id="get_order", access=AgentToolAccess.READ,
                                 action_names=("operations.order.read",),
                                 description="Read one order of the trusted store."),
            AgentToolDeclaration(tool_id="get_order_shipments", access=AgentToolAccess.READ,
                                 action_names=("operations.shipments.read",),
                                 description="Read the shipments of one order of the trusted "
                                             "store."),
            AgentToolDeclaration(tool_id="get_daily_operations_report",
                                 access=AgentToolAccess.READ,
                                 action_names=("operations.store.read", "operations.orders.list",
                                               "operations.shipments.list"),
                                 description="Read the deterministic daily operations report."),
            AgentToolDeclaration(tool_id="create_operational_ticket",
                                 access=AgentToolAccess.WRITE,
                                 action_names=("operations.ticket.create",),
                                 description="Request a governed operational ticket (only when "
                                             "the run requested this write)."),
        ),
        tool_call_limit=6,
        requirements=frozenset({"commerce.orders.read", "commerce.shipments.read",
                                "commerce.stores.read", "operations.ticketing.write",
                                "model.default"}),
        safety=frozenset({
            AgentSafetyProperty.TRUSTED_RUN_CONTEXT, AgentSafetyProperty.STORE_SCOPED,
            AgentSafetyProperty.GOVERNED_TOOLS, AgentSafetyProperty.WRITE_INTENT_REQUIRED,
            AgentSafetyProperty.UNTRUSTED_MODEL, AgentSafetyProperty.TOOL_OUTPUT_UNTRUSTED,
            AgentSafetyProperty.PRODUCT_RUN_READ_ONLY,
            AgentSafetyProperty.NO_MEMORY_KNOWLEDGE_OR_HISTORY,
            AgentSafetyProperty.NOT_EXPOSED_THROUGH_AGENTOS,
        }),
    ),
)  # fmt: skip


class ProductAgentCatalog:
    """Immutable after construction; rejects duplicates and anything but definitions."""

    __slots__ = ("_definitions",)

    def __init__(self, definitions: Iterable[AgentDefinition]) -> None:
        by_id: dict[str, AgentDefinition] = {}
        for definition in definitions:
            if not isinstance(definition, AgentDefinition):
                raise TypeError("the catalog accepts AgentDefinition values only")
            if definition.agent_id in by_id:
                raise ValueError("duplicate agent id")
            by_id[definition.agent_id] = definition
        ordered = sorted(by_id.values(), key=lambda d: (d.category.value, d.agent_id))
        object.__setattr__(self, "_definitions",
                           MappingProxyType({d.agent_id: d for d in ordered}))  # fmt: skip

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError("ProductAgentCatalog is immutable")

    def get(self, agent_id: str) -> AgentDefinition | None:
        return self._definitions.get(agent_id)

    def definitions(self) -> tuple[AgentDefinition, ...]:
        return tuple(self._definitions.values())

    @property
    def agent_ids(self) -> frozenset[str]:
        return frozenset(self._definitions)

    def __contains__(self, agent_id: object) -> bool:
        return agent_id in self._definitions

    def __len__(self) -> int:
        return len(self._definitions)


def build_default_agent_catalog() -> ProductAgentCatalog:
    """The Product business Agents of this build: exactly the Operations Agent."""
    return ProductAgentCatalog((OPERATIONS_AGENT_DEFINITION,))
