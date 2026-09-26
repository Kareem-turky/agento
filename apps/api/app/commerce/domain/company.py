"""The business entity and its stores within one isolated installation (not a tenant)."""

from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from app.commerce.domain.common import (
    DOMAIN_MODEL_CONFIG,
    CurrencyCode,
    ExternalReferences,
    NonEmptyStr,
)


class Company(BaseModel):
    model_config = DOMAIN_MODEL_CONFIG

    id: UUID = Field(default_factory=uuid4)
    name: NonEmptyStr
    external_refs: ExternalReferences = frozenset()


class Store(BaseModel):
    """A sales channel/storefront of the company. No provider configuration or credentials."""

    model_config = DOMAIN_MODEL_CONFIG

    id: UUID = Field(default_factory=uuid4)
    company_id: UUID
    name: NonEmptyStr
    currency: CurrencyCode
    # IANA name expected (e.g. "Africa/Cairo"); validated as non-empty only, so the
    # domain does not depend on the host's timezone database.
    timezone: NonEmptyStr
    external_refs: ExternalReferences = frozenset()
