"""Products and their variants."""

from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from app.commerce.domain.common import (
    DOMAIN_MODEL_CONFIG,
    ExternalReferences,
    NonEmptyStr,
    OptionalStr,
)


class ProductStatus(StrEnum):
    """Broad canonical status. Adapters map source values here and keep the original
    in ``source_status``."""

    ACTIVE = "active"
    DRAFT = "draft"
    ARCHIVED = "archived"
    UNKNOWN = "unknown"


class Product(BaseModel):
    model_config = DOMAIN_MODEL_CONFIG

    id: UUID = Field(default_factory=uuid4)
    store_id: UUID
    title: NonEmptyStr
    status: ProductStatus
    source_status: OptionalStr = None
    external_refs: ExternalReferences = frozenset()


class Variant(BaseModel):
    model_config = DOMAIN_MODEL_CONFIG

    id: UUID = Field(default_factory=uuid4)
    product_id: UUID
    title: OptionalStr = None
    sku: OptionalStr = None
    external_refs: ExternalReferences = frozenset()
