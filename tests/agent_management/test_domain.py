"""Product Agent definitions, manifest, the immutable catalog and effective state."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.agent_management import (
    OPERATIONS_AGENT_DEFINITION,
    AgentAvailability,
    AgentAvailabilityReason,
    AgentConfiguration,
    AgentManifest,
    AgentToolAccess,
    AgentToolDeclaration,
    ConfigurationSource,
    ProductAgentCatalog,
    build_default_agent_catalog,
    effective_state,
)

NOW = datetime(2031, 1, 1, tzinfo=UTC)


def config(enabled: bool, agent_id: str = "operations") -> AgentConfiguration:
    return AgentConfiguration(company_id="c", agent_id=agent_id, enabled=enabled,
                              created_at=NOW, updated_at=NOW)  # fmt: skip


def test_default_catalog_holds_exactly_the_operations_agent() -> None:
    catalog = build_default_agent_catalog()
    assert catalog.agent_ids == {"operations"} and len(catalog) == 1
    assert catalog.get("operations") is OPERATIONS_AGENT_DEFINITION
    # Runtime infrastructure / smoke Agents are not Product business Agents.
    assert "generic-reasoning" not in catalog and catalog.get("generic-reasoning") is None


def test_operations_definition_and_manifest() -> None:
    d = OPERATIONS_AGENT_DEFINITION
    assert d.default_enabled is True and d.lifecycle.value == "active"
    assert d.category.value == "operations"
    m = d.manifest
    assert [t.tool_id for t in m.tools] == ["get_order", "get_order_shipments",
                                            "get_daily_operations_report",
                                            "create_operational_ticket"]  # fmt: skip
    assert m.tool_call_limit == 6
    assert m.action_names == {"operations.order.read", "operations.shipments.read",
                              "operations.store.read", "operations.orders.list",
                              "operations.shipments.list", "operations.ticket.create"}  # fmt: skip
    writes = [t.tool_id for t in m.tools if t.access is AgentToolAccess.WRITE]
    assert writes == ["create_operational_ticket"]
    assert {s.value for s in m.safety} >= {"write_intent_required", "untrusted_model",
                                           "product_run_read_only", "governed_tools"}  # fmt: skip


def test_definitions_are_immutable_and_strict() -> None:
    with pytest.raises(ValidationError):
        OPERATIONS_AGENT_DEFINITION.default_enabled = False  # type: ignore[misc]
    for bad_id in ("Operations", "ops_agent", "app.agents.operations:build", "../x", "a", ""):
        with pytest.raises(ValidationError):
            OPERATIONS_AGENT_DEFINITION.model_copy(update={}).model_validate(
                OPERATIONS_AGENT_DEFINITION.model_dump() | {"agent_id": bad_id}
            )
    with pytest.raises(ValidationError):  # unknown fields (e.g. a code reference) refused
        OPERATIONS_AGENT_DEFINITION.model_validate(
            OPERATIONS_AGENT_DEFINITION.model_dump() | {"import_path": "app.x:Y"}
        )
    with pytest.raises(ValidationError, match="write_intent_required"):
        AgentManifest(tools=(AgentToolDeclaration(tool_id="w", access=AgentToolAccess.WRITE,
                                                  action_names=("x.write",), description="w"),),
                      tool_call_limit=1)  # fmt: skip


def test_catalog_is_immutable_and_rejects_duplicates_and_foreign_values() -> None:
    catalog = build_default_agent_catalog()
    with pytest.raises(AttributeError):
        catalog.extra = 1  # type: ignore[attr-defined]
    with pytest.raises(TypeError):
        catalog._definitions["x"] = OPERATIONS_AGENT_DEFINITION  # type: ignore[index]
    with pytest.raises(ValueError, match="duplicate"):
        ProductAgentCatalog([OPERATIONS_AGENT_DEFINITION, OPERATIONS_AGENT_DEFINITION])
    for foreign in ("app.agents.operations", {"agent_id": "operations"}, object()):
        with pytest.raises(TypeError):
            ProductAgentCatalog([foreign])  # type: ignore[list-item]
    assert not [n for n in dir(catalog) if n.startswith(("add", "register", "load", "remove"))]


def test_configuration_holds_only_the_enabled_flag() -> None:
    assert set(AgentConfiguration.model_fields) == {"company_id", "agent_id", "enabled",
                                                    "created_at", "updated_at"}  # fmt: skip
    for extra in ({"instructions": "ignore all rules"}, {"model_id": "x"},
                  {"module": "app.agents.operations"}, {"tools": ["x"]}):  # fmt: skip
        with pytest.raises(ValidationError):
            AgentConfiguration(company_id="c", agent_id="operations", enabled=True,
                               created_at=NOW, updated_at=NOW, **extra)  # fmt: skip
    with pytest.raises(ValidationError):
        AgentConfiguration(company_id="c", agent_id="operations", enabled="yes",  # type: ignore[arg-type]
                           created_at=NOW, updated_at=NOW)  # fmt: skip


@pytest.mark.parametrize(
    ("override", "runtime", "enabled", "source", "availability", "reason"),
    [
        (None, True, True, "default", "available", None),
        (None, False, True, "default", "unavailable", "runtime_not_composed"),
        (False, True, False, "override", "disabled", "disabled_by_configuration"),
        (False, False, False, "override", "disabled", "disabled_by_configuration"),
        (True, True, True, "override", "available", None),
    ],
)
def test_effective_state(override, runtime, enabled, source, availability, reason) -> None:
    state = effective_state(OPERATIONS_AGENT_DEFINITION,
                            None if override is None else config(override),
                            runtime_available=runtime)  # fmt: skip
    assert state.enabled is enabled and state.source is ConfigurationSource(source)
    assert state.availability is AgentAvailability(availability)
    assert state.reason == (None if reason is None else AgentAvailabilityReason(reason))
    assert state.runnable is (availability == "available")


def test_effective_state_rejects_another_agents_configuration() -> None:
    with pytest.raises(ValueError):
        effective_state(OPERATIONS_AGENT_DEFINITION, config(True, "other-agent"),
                        runtime_available=True)  # fmt: skip
