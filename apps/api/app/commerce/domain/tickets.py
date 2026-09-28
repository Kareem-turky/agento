"""Operational tickets: a unit of follow-up work for a store (e.g. a support case).

Minimal canonical shape only. No comments, assignees, SLAs, priorities, attachments,
conversation history or status workflow; those are later work.
"""

from enum import StrEnum
from uuid import UUID

from pydantic import AwareDatetime, BaseModel

from app.commerce.domain.common import DOMAIN_MODEL_CONFIG, ExternalReferences, NonEmptyStr


class TicketStatus(StrEnum):
    OPEN = "open"
    RESOLVED = "resolved"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"


class Ticket(BaseModel):
    """``id`` is the product-owned canonical UUID; the provider's ticket ID lives only
    in ``external_refs``."""

    model_config = DOMAIN_MODEL_CONFIG

    id: UUID
    company_id: UUID
    store_id: UUID
    title: NonEmptyStr
    description: NonEmptyStr
    status: TicketStatus
    created_at: AwareDatetime
    external_refs: ExternalReferences = frozenset()
