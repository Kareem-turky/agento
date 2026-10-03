"""PostgreSQL persistence of Product conversations (Task 037), mirroring migration 0008.

Every query carries the trusted ``company_id`` IN SQL. Ingestion is ONE transaction:
the conversation is found or created (``INSERT ... ON CONFLICT DO NOTHING`` on its unique
external binding), locked (``FOR UPDATE``) for its Product sequence, the external message
is deduplicated (after the lock, so concurrent retries see each other), appended with
the next sequence, and the conversation's last-message fields are updated. A unique
violation (a concurrent identical message on another thread) rolls everything back and
the ingest is evaluated once more against the committed state.

No provider payload, header, token or arbitrary JSON is stored. Message text is stored
as given and never logged. Every failure is a fixed-message ``ConversationRepositoryError``.
"""

from datetime import datetime
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from pydantic import ValidationError
from sqlalchemy import exc as sa_exc
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.conversations.contracts import IngestResult, ResolvedChannel
from app.conversations.delivery import (
    DeliveryOutcome,
    DeliveryResult,
    DeliveryUpdate,
    MessageDeliveryEvent,
    is_transition,
)
from app.conversations.errors import (
    ConversationRepositoryError,
    ConversationStoreConflictError,
    DeliveryEventFingerprintConflictError,
    DeliveryRefusal,
    DeliveryUpdateRefusedError,
    MessageFingerprintConflictError,
)
from app.conversations.models import (
    AuthorKind,
    Conversation,
    ConversationMessage,
    DeliveryState,
    InboundMessageEnvelope,
    MessageDirection,
    outbound_fingerprint,
)
from app.persistence.database import product_metadata

conversations = sa.Table(
    "conversations",
    product_metadata,
    sa.Column("conversation_id", sa.Uuid(), nullable=False),
    sa.Column("company_id", sa.Text(), nullable=False),
    sa.Column("store_id", sa.Text(), nullable=True),
    sa.Column("connection_id", sa.Uuid(), nullable=False),
    sa.Column("integration_id", sa.String(64), nullable=False),
    sa.Column("external_conversation_ref", sa.String(256), nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("last_message_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("last_message_id", sa.Uuid(), nullable=True),
    sa.Column("next_message_sequence", sa.Integer(), nullable=False),
    sa.PrimaryKeyConstraint("conversation_id", name="pk_conversations"),
    sa.UniqueConstraint("company_id", "conversation_id", name="uq_conversations_company"),
    sa.UniqueConstraint("company_id", "connection_id", "external_conversation_ref",
                        name="uq_conversations_external"),
    sa.Index("ix_conversations_company_activity", "company_id", "last_message_at",
             "conversation_id"),
)  # fmt: skip

conversation_messages = sa.Table(
    "conversation_messages",
    product_metadata,
    sa.Column("message_id", sa.Uuid(), nullable=False),
    sa.Column("conversation_id", sa.Uuid(), nullable=False),
    sa.Column("company_id", sa.Text(), nullable=False),
    sa.Column("connection_id", sa.Uuid(), nullable=False),
    sa.Column("sequence", sa.Integer(), nullable=False),
    sa.Column("direction", sa.String(16), nullable=False),
    sa.Column("author_kind", sa.String(16), nullable=False),
    sa.Column("external_message_ref", sa.String(256), nullable=True),
    sa.Column("external_sender_ref", sa.String(256), nullable=True),
    sa.Column("text", sa.Text(), nullable=False),
    sa.Column("content_fingerprint", sa.CHAR(64), nullable=False),
    sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("delivery_state", sa.String(16), nullable=False),
    sa.Column("created_by_actor_id", sa.Text(), nullable=True),
    sa.Column("created_by_actor_type", sa.String(32), nullable=True),
    sa.PrimaryKeyConstraint("message_id", name="pk_conversation_messages"),
    sa.UniqueConstraint("company_id", "message_id", name="uq_conversation_messages_company"),
    sa.UniqueConstraint("conversation_id", "sequence", name="uq_conversation_messages_sequence"),
    sa.UniqueConstraint("company_id", "connection_id", "external_message_ref",
                        name="uq_conversation_messages_external"),
    sa.ForeignKeyConstraint(
        ["company_id", "conversation_id"],
        ["product.conversations.company_id", "product.conversations.conversation_id"],
        name="fk_conversation_messages_conversation",
    ),
)  # fmt: skip

message_delivery_events = sa.Table(
    "message_delivery_events",
    product_metadata,
    sa.Column("message_id", sa.Uuid(), nullable=False),
    sa.Column("company_id", sa.Text(), nullable=False),
    sa.Column("sequence", sa.Integer(), nullable=False),
    sa.Column("state", sa.String(16), nullable=False),
    sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("external_event_ref", sa.String(256), nullable=True),
    sa.PrimaryKeyConstraint("message_id", "sequence", name="pk_message_delivery_events"),
    sa.UniqueConstraint("company_id", "message_id", "external_event_ref",
                        name="uq_message_delivery_events_external"),
    sa.ForeignKeyConstraint(
        ["company_id", "message_id"],
        ["product.conversation_messages.company_id", "product.conversation_messages.message_id"],
        name="fk_message_delivery_events_message",
    ),
)  # fmt: skip

_c = conversations.c
_m = conversation_messages.c
_d = message_delivery_events.c
_ERRORS = (sa_exc.SQLAlchemyError, OSError)
_INGEST_ATTEMPTS = 2


def _conversation(row: sa.RowMapping) -> Conversation:
    try:
        return Conversation.model_validate({k: row[k] for k in Conversation.model_fields})
    except (ValidationError, KeyError):
        raise ConversationRepositoryError() from None


def _message(row: sa.RowMapping) -> ConversationMessage:
    try:
        return ConversationMessage.model_validate(
            {k: row[k] for k in ConversationMessage.model_fields})  # fmt: skip
    except (ValidationError, KeyError):
        raise ConversationRepositoryError() from None


def _event(row: sa.RowMapping) -> MessageDeliveryEvent:
    try:
        return MessageDeliveryEvent.model_validate(
            {k: row[k] for k in MessageDeliveryEvent.model_fields})  # fmt: skip
    except (ValidationError, KeyError):
        raise ConversationRepositoryError() from None


class PostgresConversationRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = session_factory

    # ----- inbound ---------------------------------------------------------------------------

    async def ingest(
        self, channel: ResolvedChannel, envelope: InboundMessageEnvelope, *,
        fingerprint: str, new_conversation_id: UUID, new_message_id: UUID, now: datetime,
    ) -> IngestResult:  # fmt: skip
        for attempt in range(_INGEST_ATTEMPTS):
            try:
                return await self._ingest_once(
                    channel, envelope, fingerprint, new_conversation_id, new_message_id, now
                )
            except sa_exc.IntegrityError:
                if attempt + 1 == _INGEST_ATTEMPTS:
                    raise ConversationRepositoryError() from None
            except _ERRORS:
                raise ConversationRepositoryError() from None
        raise ConversationRepositoryError()  # unreachable

    @staticmethod
    async def _existing(session: AsyncSession, channel: ResolvedChannel,
                        envelope: InboundMessageEnvelope,
                        fingerprint: str) -> IngestResult | None:  # fmt: skip
        row = (await session.execute(sa.select(conversation_messages).where(
            _m.company_id == channel.company_id, _m.connection_id == channel.connection_id,
            _m.external_message_ref == envelope.external_message_ref,
        ))).mappings().first()  # fmt: skip
        if row is None:
            return None
        if row["content_fingerprint"] != fingerprint:
            raise MessageFingerprintConflictError()
        conversation = (await session.execute(sa.select(conversations).where(
            _c.company_id == channel.company_id, _c.conversation_id == row["conversation_id"],
        ))).mappings().one()  # fmt: skip
        return IngestResult(_conversation(conversation), _message(row), replayed=True)

    async def _ingest_once(self, channel: ResolvedChannel, envelope: InboundMessageEnvelope,
                           fingerprint: str, new_conversation_id: UUID, new_message_id: UUID,
                           now: datetime) -> IngestResult:  # fmt: skip
        async with self._sessions() as session, session.begin():
            replay = await self._existing(session, channel, envelope, fingerprint)
            if replay is not None:
                return replay
            await session.execute(pg_insert(conversations).values(
                conversation_id=new_conversation_id, company_id=channel.company_id,
                store_id=channel.store_id, connection_id=channel.connection_id,
                integration_id=channel.integration_id,
                external_conversation_ref=envelope.external_conversation_ref,
                created_at=now, last_message_at=now, last_message_id=None,
                next_message_sequence=1,
            ).on_conflict_do_nothing(constraint="uq_conversations_external"))  # fmt: skip
            locked = (await session.execute(sa.select(conversations).where(
                _c.company_id == channel.company_id, _c.connection_id == channel.connection_id,
                _c.external_conversation_ref == envelope.external_conversation_ref,
            ).with_for_update())).mappings().one()  # fmt: skip
            if locked["store_id"] != channel.store_id:
                raise ConversationStoreConflictError()
            # A concurrent identical delivery may have committed while we waited.
            replay = await self._existing(session, channel, envelope, fingerprint)
            if replay is not None:
                return replay
            sequence = locked["next_message_sequence"]
            message = ConversationMessage(
                message_id=new_message_id, conversation_id=locked["conversation_id"],
                company_id=channel.company_id, connection_id=channel.connection_id,
                sequence=sequence, direction=MessageDirection.INBOUND,
                author_kind=AuthorKind.EXTERNAL,
                external_message_ref=envelope.external_message_ref,
                external_sender_ref=envelope.external_sender_ref, text=envelope.text,
                content_fingerprint=fingerprint, occurred_at=envelope.occurred_at,
                recorded_at=now, delivery_state=DeliveryState.RECEIVED,
            )  # fmt: skip
            await session.execute(sa.insert(conversation_messages).values(_message_row(message)))
            updated = (await session.execute(
                sa.update(conversations)
                .where(_c.company_id == channel.company_id,
                       _c.conversation_id == locked["conversation_id"],
                       _c.next_message_sequence == sequence)
                .values(next_message_sequence=sequence + 1, last_message_at=now,
                        last_message_id=new_message_id)
                .returning(*conversations.c)
            )).mappings().one()  # fmt: skip
            return IngestResult(_conversation(updated), message, replayed=False)

    # ----- outbound (future seam) ------------------------------------------------------------

    async def append_outbound(
        self, company_id: str, conversation_id: UUID, *, message_id: UUID, text: str,
        author_kind: AuthorKind, actor_id: str | None, actor_type: str | None,
        now: datetime,
    ) -> ConversationMessage | None:  # fmt: skip
        try:
            async with self._sessions() as session, session.begin():
                locked = (await session.execute(sa.select(conversations).where(
                    _c.company_id == company_id, _c.conversation_id == conversation_id,
                ).with_for_update())).mappings().first()  # fmt: skip
                if locked is None:
                    return None
                sequence = locked["next_message_sequence"]
                message = ConversationMessage(
                    message_id=message_id, conversation_id=conversation_id,
                    company_id=company_id, connection_id=locked["connection_id"],
                    sequence=sequence, direction=MessageDirection.OUTBOUND,
                    author_kind=author_kind, text=text,
                    content_fingerprint=outbound_fingerprint(message_id, text),
                    occurred_at=now, recorded_at=now, delivery_state=DeliveryState.PENDING,
                    created_by_actor_id=actor_id, created_by_actor_type=actor_type,
                )  # fmt: skip
                await session.execute(
                    sa.insert(conversation_messages).values(_message_row(message))
                )
                await session.execute(
                    sa.update(conversations)
                    .where(_c.company_id == company_id, _c.conversation_id == conversation_id)
                    .values(next_message_sequence=sequence + 1, last_message_at=now,
                            last_message_id=message_id))  # fmt: skip
                return message
        except _ERRORS:
            raise ConversationRepositoryError() from None

    async def record_delivery(
        self, company_id: str, message_id: UUID, update: DeliveryUpdate, now: datetime
    ) -> DeliveryResult:
        try:
            async with self._sessions() as session, session.begin():
                row = (await session.execute(sa.select(conversation_messages).where(
                    _m.company_id == company_id, _m.message_id == message_id,
                ).with_for_update())).mappings().first()  # fmt: skip
                if row is None:
                    raise DeliveryUpdateRefusedError(DeliveryRefusal.MESSAGE_NOT_FOUND)
                if row["direction"] != MessageDirection.OUTBOUND.value:
                    raise DeliveryUpdateRefusedError(DeliveryRefusal.NOT_OUTBOUND)
                current = DeliveryState(row["delivery_state"])
                if update.external_event_ref is not None:
                    seen = (await session.execute(sa.select(message_delivery_events).where(
                        _d.company_id == company_id, _d.message_id == message_id,
                        _d.external_event_ref == update.external_event_ref,
                    ))).mappings().first()  # fmt: skip
                    if seen is not None:
                        recorded = (seen["state"], seen["occurred_at"])
                        if recorded != (update.state.value, update.occurred_at):
                            raise DeliveryEventFingerprintConflictError()
                        return DeliveryResult(outcome=DeliveryOutcome.DUPLICATE, state=current)
                if not is_transition(current, update.state):
                    return DeliveryResult(outcome=DeliveryOutcome.STALE, state=current)
                next_sequence = (
                    sa.select(sa.func.coalesce(sa.func.max(_d.sequence), 0) + 1)
                    .where(_d.message_id == message_id)
                    .scalar_subquery()
                )
                await session.execute(sa.insert(message_delivery_events).values(
                    message_id=message_id, company_id=company_id, sequence=next_sequence,
                    state=update.state.value, occurred_at=update.occurred_at, recorded_at=now,
                    external_event_ref=update.external_event_ref,
                ))  # fmt: skip
                await session.execute(
                    sa.update(conversation_messages)
                    .where(_m.company_id == company_id, _m.message_id == message_id)
                    .values(delivery_state=update.state.value))  # fmt: skip
                return DeliveryResult(outcome=DeliveryOutcome.APPLIED, state=update.state)
        except _ERRORS:
            raise ConversationRepositoryError() from None

    # ----- reads -------------------------------------------------------------------------------

    async def list_conversations(
        self, company_id: str, *, store_ids: frozenset[str], connection_id: UUID | None,
        limit: int,
    ) -> tuple[Conversation, ...]:  # fmt: skip
        visible = _c.store_id.is_(None)
        if store_ids:
            visible = sa.or_(visible, _c.store_id.in_(sorted(store_ids)))
        query = (sa.select(conversations).where(_c.company_id == company_id, visible)
                 .order_by(_c.last_message_at.desc(), _c.conversation_id).limit(limit))  # fmt: skip
        if connection_id is not None:
            query = query.where(_c.connection_id == connection_id)
        rows = await self._rows(query)
        return tuple(_conversation(r) for r in rows)

    async def get_conversation(self, company_id: str, conversation_id: UUID) -> Conversation | None:
        rows = await self._rows(sa.select(conversations).where(
            _c.company_id == company_id, _c.conversation_id == conversation_id))  # fmt: skip
        return _conversation(rows[0]) if rows else None

    async def list_messages(
        self, company_id: str, conversation_id: UUID, *, before_sequence: int | None,
        limit: int,
    ) -> tuple[ConversationMessage, ...]:  # fmt: skip
        query = sa.select(conversation_messages).where(
            _m.company_id == company_id, _m.conversation_id == conversation_id
        )
        if before_sequence is not None:
            query = query.where(_m.sequence < before_sequence)
        rows = await self._rows(query.order_by(_m.sequence.desc()).limit(limit))
        return tuple(_message(r) for r in reversed(rows))

    async def delivery_events(
        self, company_id: str, message_id: UUID
    ) -> tuple[MessageDeliveryEvent, ...]:
        rows = await self._rows(sa.select(message_delivery_events).where(
            _d.company_id == company_id, _d.message_id == message_id,
        ).order_by(_d.sequence))  # fmt: skip
        return tuple(_event(r) for r in rows)

    async def _rows(self, query: sa.Select[Any]) -> list[sa.RowMapping]:
        try:
            async with self._sessions() as session:
                return list((await session.execute(query)).mappings().all())
        except _ERRORS:
            raise ConversationRepositoryError() from None


def _message_row(message: ConversationMessage) -> dict[str, Any]:
    return {**message.model_dump(), "direction": message.direction.value,
            "author_kind": message.author_kind.value,
            "delivery_state": message.delivery_state.value}  # fmt: skip
