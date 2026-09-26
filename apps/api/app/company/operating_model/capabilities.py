"""Product capabilities the company intends to enable.

Declarative only: this never imports, builds or registers any agent.
"""

from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel

from app.commerce.domain.common import DOMAIN_MODEL_CONFIG
from app.company.operating_model.common import SortedJson


class AgentCapability(StrEnum):
    OPERATIONS = "operations"
    FINANCE = "finance"
    MARKETING = "marketing"
    CUSTOMER_EXPERIENCE = "customer_experience"
    ANALYTICS = "analytics"
    GROWTH = "growth"


class CapabilitiesConfig(BaseModel):
    model_config = DOMAIN_MODEL_CONFIG

    enabled_agents: Annotated[frozenset[AgentCapability], SortedJson] = frozenset()
