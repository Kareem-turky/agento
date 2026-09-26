import pytest
from pydantic import ValidationError

from app.company.operating_model import (
    AgentCapability,
    CapabilitiesConfig,
    KPIConfig,
    KPIKey,
    ReportingConfig,
    ReportPeriod,
)


def test_kpi_keys_are_operational_only() -> None:
    assert {k.value for k in KPIKey} == {
        "orders_created", "orders_confirmed", "orders_fulfilled", "orders_cancelled",
        "late_orders", "fulfillment_rate", "shipments_created", "shipments_delivered",
        "late_shipments", "inventory_low_items",
    }  # fmt: skip


def test_kpi_config_defaults_and_ordering() -> None:
    assert KPIConfig() == KPIConfig(enabled_kpis=[], primary_kpis=[])
    config = KPIConfig(
        enabled_kpis=["late_orders", "orders_created", "fulfillment_rate"],
        primary_kpis=["fulfillment_rate", "late_orders"],
    )

    assert isinstance(config.enabled_kpis, frozenset)
    assert config.primary_kpis == (KPIKey.FULFILLMENT_RATE, KPIKey.LATE_ORDERS)
    assert config.model_dump(mode="json") == {
        "enabled_kpis": ["fulfillment_rate", "late_orders", "orders_created"],
        "primary_kpis": ["fulfillment_rate", "late_orders"],
    }


def test_primary_kpis_must_be_enabled() -> None:
    with pytest.raises(ValidationError, match="not enabled"):
        KPIConfig(enabled_kpis=["late_orders"], primary_kpis=["fulfillment_rate"])


def test_duplicate_primary_kpis_are_rejected() -> None:
    with pytest.raises(ValidationError, match="duplicates"):
        KPIConfig(enabled_kpis=["late_orders"], primary_kpis=["late_orders", "late_orders"])


@pytest.mark.parametrize("bad", ["revenue", "roas", "gross_margin", "custom_kpi"])
def test_unknown_kpis_are_rejected(bad: str) -> None:
    with pytest.raises(ValidationError):
        KPIConfig(enabled_kpis=[bad])


def test_reporting_defaults_and_values() -> None:
    config = ReportingConfig(timezone="UTC")

    assert config.default_period is ReportPeriod.YESTERDAY
    assert config.include_comparison is True
    assert config.max_highlights == 5
    assert {p.value for p in ReportPeriod} == {"today", "yesterday", "last_7_days", "last_30_days"}
    assert ReportingConfig(timezone="Europe/Berlin", max_highlights=100).max_highlights == 100


@pytest.mark.parametrize(
    "overrides",
    [
        {"timezone": ""},
        {"timezone": "   "},
        {"default_period": "last_week"},
        {"include_comparison": "true"},
        {"max_highlights": 0},
        {"max_highlights": 101},
        {"max_highlights": 5.0},
        {"max_highlights": "5"},
        {"recipients": ["someone"]},
    ],
)
def test_reporting_rejects_invalid_values(overrides: dict) -> None:
    with pytest.raises(ValidationError):
        ReportingConfig(**{"timezone": "UTC", **overrides})


def test_reporting_requires_timezone() -> None:
    with pytest.raises(ValidationError):
        ReportingConfig()


def test_capabilities_are_a_declarative_set() -> None:
    assert {c.value for c in AgentCapability} == {
        "operations", "finance", "marketing", "customer_experience", "analytics", "growth",
    }  # fmt: skip
    assert CapabilitiesConfig().enabled_agents == frozenset()
    config = CapabilitiesConfig(enabled_agents=["operations", "analytics", "operations"])

    assert config.enabled_agents == {AgentCapability.OPERATIONS, AgentCapability.ANALYTICS}
    assert config.model_dump(mode="json") == {"enabled_agents": ["analytics", "operations"]}


@pytest.mark.parametrize("bad", [{"enabled_agents": ["support_bot"]}, {"agent_ids": ["x"]}])
def test_capabilities_reject_unknown_values(bad: dict) -> None:
    with pytest.raises(ValidationError):
        CapabilitiesConfig(**bad)


@pytest.mark.parametrize(
    ("model", "field", "value"),
    [
        (KPIConfig, "primary_kpis", ()),
        (ReportingConfig, "max_highlights", 10),
        (CapabilitiesConfig, "enabled_agents", frozenset()),
    ],
)
def test_configs_are_frozen(model: type, field: str, value: object) -> None:
    config = model(timezone="UTC") if model is ReportingConfig else model()
    with pytest.raises(ValidationError):
        setattr(config, field, value)
