"""Deterministic canonical IDs for mock provider records.

Canonical IDs are product-owned UUIDs, never provider IDs. With no persistence yet,
the mock adapter derives them with UUID5 from a fixed namespace, the entity type and
the provider key, so the same record always maps to the same UUID and different
entity types never collide. Real adapters may later persist identity mappings.
"""

from enum import StrEnum
from uuid import UUID, uuid5

MOCK_SYSTEM_ID = "mock-commerce"

# Fixed, arbitrary namespace owned by this product for the mock adapter. Never change it.
MOCK_ID_NAMESPACE = UUID("5a0b7c3e-6f2d-4d8e-9b1a-3c4e5f607182")


class EntityType(StrEnum):
    COMPANY = "company"
    STORE = "store"
    CUSTOMER = "customer"
    PRODUCT = "product"
    VARIANT = "variant"
    WAREHOUSE = "warehouse"
    ORDER = "order"
    ORDER_ITEM = "order_item"
    SHIPMENT = "shipment"
    INVENTORY_LEVEL = "inventory_level"


def canonical_id(entity: EntityType, external_id: str) -> UUID:
    return uuid5(MOCK_ID_NAMESPACE, f"{entity.value}:{external_id}")
