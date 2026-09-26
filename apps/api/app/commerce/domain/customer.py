"""Customer of a store. Deliberately light: no CRM behaviour.

Optional contact fields are not format-validated (external systems hold imperfect
real-world data); blank values normalize to ``None`` and others are trimmed.
"""

from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from app.commerce.domain.common import DOMAIN_MODEL_CONFIG, ExternalReferences, OptionalStr


class Customer(BaseModel):
    model_config = DOMAIN_MODEL_CONFIG

    id: UUID = Field(default_factory=uuid4)
    store_id: UUID
    name: OptionalStr = None
    email: OptionalStr = None
    phone: OptionalStr = None
    external_refs: ExternalReferences = frozenset()
