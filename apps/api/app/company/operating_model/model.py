"""The Company Operating Model: how one company operates the canonical commerce domain.

Core invariants (quantities > 0, Decimal money, aware timestamps, trusted identity)
stay in the domain code. This model holds company-specific configuration only. It is
not provider configuration, authentication or permission policy, and it executes
nothing. Persistence and version history come later.
"""

from typing import Self
from uuid import UUID

from pydantic import BaseModel, Field, StrictInt, model_validator

from app.commerce.domain.common import DOMAIN_MODEL_CONFIG
from app.company.operating_model.capabilities import CapabilitiesConfig
from app.company.operating_model.escalation import EscalationRule
from app.company.operating_model.kpi import KPIConfig
from app.company.operating_model.reporting import ReportingConfig
from app.company.operating_model.sla import OrderSLAConfig, ShipmentSLAConfig


class CompanyOperatingModel(BaseModel):
    model_config = DOMAIN_MODEL_CONFIG

    company_id: UUID  # the canonical Company.id this configuration belongs to
    version: StrictInt = Field(ge=1)
    order_sla: OrderSLAConfig
    shipment_sla: ShipmentSLAConfig
    escalations: tuple[EscalationRule, ...] = ()
    kpis: KPIConfig
    reporting: ReportingConfig
    capabilities: CapabilitiesConfig

    @model_validator(mode="after")
    def _escalation_ids_are_unique(self) -> Self:
        ids = [rule.id for rule in self.escalations]
        duplicates = sorted({rule_id for rule_id in ids if ids.count(rule_id) > 1})
        if duplicates:
            raise ValueError(f"escalation rule ids must be unique; duplicated: {duplicates}")
        return self
