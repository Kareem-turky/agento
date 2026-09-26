"""Company Operating Model: provider-independent, immutable company configuration."""

from app.company.operating_model.capabilities import AgentCapability, CapabilitiesConfig
from app.company.operating_model.common import Duration
from app.company.operating_model.escalation import (
    CountThreshold,
    DurationThreshold,
    EscalationCondition,
    EscalationRule,
    EscalationSeverity,
    QuantityThreshold,
)
from app.company.operating_model.kpi import KPIConfig, KPIKey
from app.company.operating_model.model import CompanyOperatingModel
from app.company.operating_model.reporting import ReportingConfig, ReportPeriod
from app.company.operating_model.sla import (
    DEFAULT_TERMINAL_SHIPMENT_STATUSES,
    OrderSLAConfig,
    ShipmentSLAConfig,
)

__all__ = [
    "DEFAULT_TERMINAL_SHIPMENT_STATUSES",
    "AgentCapability",
    "CapabilitiesConfig",
    "CompanyOperatingModel",
    "CountThreshold",
    "Duration",
    "DurationThreshold",
    "EscalationCondition",
    "EscalationRule",
    "EscalationSeverity",
    "KPIConfig",
    "KPIKey",
    "OrderSLAConfig",
    "QuantityThreshold",
    "ReportPeriod",
    "ReportingConfig",
    "ShipmentSLAConfig",
]
