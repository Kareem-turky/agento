"""Warehouses and inventory levels.

Inventory semantics differ between systems, so no relationship between
``available``, ``on_hand`` and ``reserved`` is enforced, and quantities may be
negative (backorders and overselling are real).
"""

from uuid import UUID, uuid4

from pydantic import AwareDatetime, BaseModel, Field

from app.commerce.domain.common import (
    DOMAIN_MODEL_CONFIG,
    DecimalValue,
    ExternalReferences,
    NonEmptyStr,
)


class Warehouse(BaseModel):
    model_config = DOMAIN_MODEL_CONFIG

    id: UUID = Field(default_factory=uuid4)
    company_id: UUID
    name: NonEmptyStr
    external_refs: ExternalReferences = frozenset()


class InventoryLevel(BaseModel):
    model_config = DOMAIN_MODEL_CONFIG

    id: UUID = Field(default_factory=uuid4)
    variant_id: UUID
    warehouse_id: UUID
    available: DecimalValue
    on_hand: DecimalValue | None = None
    reserved: DecimalValue | None = None
    updated_at: AwareDatetime | None = None
