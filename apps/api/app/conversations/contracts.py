"""The Conversation repository contract (Task 037). Every call is scoped by the TRUSTED
company id; another company's rows are indistinguishable from none."""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable
from uuid import UUID

from app.context.models import ActorType
from app.conversations.delivery import DeliveryResult, DeliveryUpdate, MessageDeliveryEvent
from app.conversations.models import (
    AuthorKind,
    Conversation,
    ConversationMessage,
    InboundMessageEnvelope,
)


@dataclass(frozen=True, slots=True)
class ResolvedChannel:
    """A TRUSTED channel, resolved from Product integration state by the ingress."""

    company_id: str
    store_id: str | None
    connection_id: UUID
    integration_id: str


@dataclass(frozen=True, slots=True)
class IngestResult:
    conversation: Conversation
    message: ConversationMessage
    replayed: bool  # True: the external message was already recorded identically


@runtime_checkable
class ConversationRepository(Protocol):
    async def ingest(
        self, channel: ResolvedChannel, envelope: InboundMessageEnvelope, *,
        fingerprint: str, new_conversation_id: UUID, new_message_id: UUID, now: datetime,
    ) -> IngestResult:  # fmt: skip
        """ONE transaction: find or create the conversation (unique per company,
        connection and external conversation ref), deduplicate the external message
        (unique per company, connection and external message ref), append it with the
        next sequence (row lock) and update the conversation's last-message fields.

        Same external message ref + same fingerprint -> the stored message (replayed).
        Same ref + different fingerprint -> ``MessageFingerprintConflictError``.
        A different trusted store -> ``ConversationStoreConflictError``.
        Storage failure -> ``ConversationRepositoryError`` (nothing recorded)."""
        ...

    async def append_outbound(
        self, company_id: str, conversation_id: UUID, *, message_id: UUID, text: str,
        author_kind: AuthorKind, actor_id: str | None, actor_type: ActorType | None,
        now: datetime,
    ) -> ConversationMessage | None:  # fmt: skip
        """The FUTURE outbound seam: a ``pending`` outbound message with the next
        sequence (no caller in this build: there is no send action). None if the
        conversation is not this company's."""
        ...

    async def record_delivery(
        self, company_id: str, message_id: UUID, update: DeliveryUpdate, now: datetime
    ) -> DeliveryResult:
        """Apply a delivery update atomically (row lock on the message): a repeat of a
        recorded external event ref is a DUPLICATE (``DeliveryEventFingerprintConflictError``
        if its state or occurred_at differs); a forward transition appends one applied
        event and updates the message; anything else is STALE: the message state is
        unchanged, and the observation is appended (``applied=False``) only when it
        carries an external event ref. Raises
        ``DeliveryUpdateRefusedError`` for an unknown/foreign or inbound message."""
        ...

    async def list_conversations(
        self, company_id: str, *, store_ids: frozenset[str], connection_id: UUID | None,
        limit: int,
    ) -> tuple[Conversation, ...]:  # fmt: skip
        """Newest activity first (``last_message_at`` DESC, then ``conversation_id``).
        Only store-less conversations and those of ``store_ids`` (in SQL)."""
        ...

    async def get_conversation(
        self, company_id: str, conversation_id: UUID
    ) -> Conversation | None: ...

    async def list_messages(
        self, company_id: str, conversation_id: UUID, *, before_sequence: int | None,
        limit: int,
    ) -> tuple[ConversationMessage, ...]:  # fmt: skip
        """At most ``limit`` messages with ``sequence < before_sequence`` (if given),
        the NEWEST of them, returned in ascending sequence order."""
        ...

    async def delivery_events(
        self, company_id: str, message_id: UUID
    ) -> tuple[MessageDeliveryEvent, ...]: ...
