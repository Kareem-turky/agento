"""Canonical Conversation and ConversationMessage models (Task 037).

Provider-independent by construction: there is no provider name, provider payload,
header, token or arbitrary metadata field anywhere. External identifiers are OPAQUE
(bounded, printable, case-sensitive, never interpreted, normalized, fetched or used as
an import path, SQL or Product identity). Content is plain text only, at most
``MAX_TEXT_CHARS`` characters.

``sequence`` is the Product append order of a conversation (1, 2, 3, ...), not a claim
about the provider's causal order: a late event may carry an earlier ``occurred_at``
(the source time) than a message with a smaller sequence. ``recorded_at`` is the Product
clock at ingestion.
"""

import hashlib
import json
import unicodedata
from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Self
from uuid import UUID

from pydantic import (
    AfterValidator,
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)

from app.context.models import ActorType

MAX_TEXT_CHARS = 16_000
MAX_EXTERNAL_REF_CHARS = 256
FINGERPRINT_VERSION = "conversation-inbound-v1"
_FROZEN = ConfigDict(frozen=True, extra="forbid")


def _text(value: str) -> str:
    """Printable plain text: no control characters except newline and tab, not blank."""
    if any(unicodedata.category(ch) == "Cc" and ch not in "\n\t" for ch in value):
        raise ValueError("control characters are not allowed")
    if not value.strip():
        raise ValueError("blank text is not allowed")
    return value


# Opaque external reference: visible ASCII only (no spaces, controls or Unicode tricks).
ExternalRef = Annotated[
    str,
    StringConstraints(strict=True, min_length=1, max_length=MAX_EXTERNAL_REF_CHARS,
                      pattern=r"^[\x21-\x7e]+$"),
]  # fmt: skip
MessageText = Annotated[
    str, StringConstraints(strict=True, min_length=1, max_length=MAX_TEXT_CHARS),
    AfterValidator(_text),
]  # fmt: skip
ScopedId = Annotated[str, StringConstraints(strict=True, min_length=1, max_length=256)]
IntegrationRef = Annotated[
    str, StringConstraints(strict=True, pattern=r"^[a-z0-9][a-z0-9-]{0,62}[a-z0-9]$")
]
Fingerprint = Annotated[str, StringConstraints(strict=True, pattern=r"^[0-9a-f]{64}$")]


class MessageDirection(StrEnum):
    INBOUND = "inbound"
    OUTBOUND = "outbound"


class AuthorKind(StrEnum):
    """Descriptive metadata only, never authorization. Inbound messages are EXTERNAL."""

    EXTERNAL = "external"
    HUMAN = "human"
    AGENT = "agent"
    SYSTEM = "system"


class DeliveryState(StrEnum):
    RECEIVED = "received"  # inbound only
    PENDING = "pending"
    ACCEPTED = "accepted"
    SENT = "sent"
    DELIVERED = "delivered"
    FAILED = "failed"
    UNKNOWN = "unknown"


class Conversation(BaseModel):
    """One external thread on one IntegrationConnection (unique per company, connection
    and external conversation reference). Never merged across connections."""

    model_config = _FROZEN

    conversation_id: UUID
    company_id: ScopedId
    store_id: ScopedId | None = None
    connection_id: UUID  # historical correlation: survives the connection's deletion
    integration_id: IntegrationRef
    external_conversation_ref: ExternalRef
    created_at: AwareDatetime
    last_message_at: AwareDatetime
    last_message_id: UUID | None = None


class ConversationMessage(BaseModel):
    model_config = _FROZEN

    message_id: UUID
    conversation_id: UUID
    company_id: ScopedId
    connection_id: UUID
    sequence: int = Field(ge=1)
    direction: MessageDirection
    author_kind: AuthorKind
    external_message_ref: ExternalRef | None = None
    external_sender_ref: ExternalRef | None = None
    text: MessageText
    content_fingerprint: Fingerprint
    occurred_at: AwareDatetime  # the source time (provider event)
    recorded_at: AwareDatetime  # the Product clock
    delivery_state: DeliveryState
    created_by_actor_id: ScopedId | None = None
    created_by_actor_type: ActorType | None = None

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        inbound = self.direction is MessageDirection.INBOUND
        if inbound != (self.delivery_state is DeliveryState.RECEIVED):
            raise ValueError("inbound messages are 'received'; outbound never are")
        if inbound and (self.author_kind is not AuthorKind.EXTERNAL
                        or self.external_message_ref is None):  # fmt: skip
            raise ValueError("an inbound message is external and has an external reference")
        if (self.created_by_actor_id is None) != (self.created_by_actor_type is None):
            raise ValueError("inconsistent creator")
        if inbound and self.created_by_actor_id is not None:
            raise ValueError("an inbound message has no Product creator")
        return self


class InboundMessageEnvelope(BaseModel):
    """What a provider adapter maps a validated external event to. It carries NO
    company, store, connection or Product identity: those come from the trusted
    ``ChannelContext``, never from external payload data."""

    model_config = _FROZEN

    external_conversation_ref: ExternalRef
    external_message_ref: ExternalRef
    external_sender_ref: ExternalRef | None = None
    text: MessageText
    occurred_at: AwareDatetime


class ChannelContext(BaseModel):
    """TRUSTED Product context of an inbound call, supplied by Product code (a reviewed
    provider ingress route), never parsed from the external payload."""

    model_config = _FROZEN

    company_id: ScopedId
    connection_id: UUID
    store_id: ScopedId | None = None


def _utc(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat()


def inbound_fingerprint(connection_id: UUID, envelope: InboundMessageEnvelope) -> str:
    """Deterministic SHA-256 of the canonical inbound content, used only to tell an
    identical provider retry from a conflicting one. Never authorization."""
    canonical = {
        "version": FINGERPRINT_VERSION,
        "connection_id": str(connection_id),
        "external_conversation_ref": envelope.external_conversation_ref,
        "external_message_ref": envelope.external_message_ref,
        "external_sender_ref": envelope.external_sender_ref,
        "text": envelope.text,
        "occurred_at": _utc(envelope.occurred_at),
    }
    text = json.dumps(canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def outbound_fingerprint(message_id: UUID, text: str) -> str:
    """SHA-256 of a Product-authored outbound message (integrity metadata only)."""
    canonical = {"version": "conversation-outbound-v1", "message_id": str(message_id),
                 "text": text}  # fmt: skip
    encoded = json.dumps(canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
