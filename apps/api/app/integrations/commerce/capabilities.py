"""What an integration can do. Descriptive metadata, not authorization."""

from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, StringConstraints

from app.commerce.domain.common import DOMAIN_MODEL_CONFIG, NonEmptyStr


class IntegrationCapability(StrEnum):
    """Only capabilities that are implemented. Write capabilities do not exist yet."""

    ORDERS_READ = "orders_read"
    SHIPMENTS_READ = "shipments_read"
    INVENTORY_READ = "inventory_read"


# Stable machine identifier, e.g. "mock-commerce".
IntegrationId = Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9._-]{0,63}$")]


class IntegrationDescriptor(BaseModel):
    model_config = DOMAIN_MODEL_CONFIG

    id: IntegrationId
    name: NonEmptyStr
    capabilities: frozenset[IntegrationCapability]
