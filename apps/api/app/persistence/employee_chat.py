"""PostgreSQL persistence of Employee Chat (Task 042), mirroring migration 0009.

Every query carries the trusted ``company_id`` (and, for threads, the actor id and type)
IN SQL. A new turn is ONE transaction: the existing turn with this id is returned as is,
otherwise the owning thread is locked (``FOR UPDATE``) for its next Product sequence and
a PENDING turn is recorded; a concurrent identical submission loses on the primary key
and reads the winner. Completing a turn and storing its proposal is one transaction.
Proposal confirmation and cancellation are compare-and-set updates on ``state`` (one
winner).

Message, answer, title and description text is stored as given and never logged. Every
failure is a fixed-message ``ChatRepositoryError``.
"""

from datetime import datetime
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from pydantic import ValidationError
from sqlalchemy import exc as sa_exc
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.employee_chat.contracts import BeginTurnResult, CancelOutcome, ClaimOutcome
from app.employee_chat.errors import ChatRepositoryError
from app.employee_chat.models import (
    ChatThread,
    ChatTurn,
    ProposalState,
    TicketProposal,
    TurnFailure,
    TurnStatus,
)
from app.persistence.database import product_metadata

chat_threads = sa.Table(
    "chat_threads",
    product_metadata,
    sa.Column("thread_id", sa.Uuid(), nullable=False),
    sa.Column("company_id", sa.Text(), nullable=False),
    sa.Column("actor_id", sa.Text(), nullable=False),
    sa.Column("actor_type", sa.String(32), nullable=False),
    sa.Column("store_id", sa.Text(), nullable=False),
    sa.Column("agent_id", sa.String(64), nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("next_turn_sequence", sa.Integer(), nullable=False),
    sa.PrimaryKeyConstraint("thread_id", name="pk_chat_threads"),
    sa.UniqueConstraint("company_id", "thread_id", name="uq_chat_threads_company"),
    sa.Index(
        "ix_chat_threads_owner", "company_id", "actor_id", "actor_type", "store_id", "updated_at"
    ),
)

chat_turns = sa.Table(
    "chat_turns",
    product_metadata,
    sa.Column("turn_id", sa.Uuid(), nullable=False),
    sa.Column("thread_id", sa.Uuid(), nullable=False),
    sa.Column("company_id", sa.Text(), nullable=False),
    sa.Column("sequence", sa.Integer(), nullable=False),
    sa.Column("user_text", sa.Text(), nullable=False),
    sa.Column("assistant_text", sa.Text(), nullable=True),
    sa.Column("status", sa.String(16), nullable=False),
    sa.Column("failure", sa.String(32), nullable=True),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    sa.PrimaryKeyConstraint("turn_id", name="pk_chat_turns"),
    sa.UniqueConstraint("company_id", "turn_id", name="uq_chat_turns_company"),
    sa.UniqueConstraint("thread_id", "sequence", name="uq_chat_turns_sequence"),
    sa.ForeignKeyConstraint(
        ["company_id", "thread_id"],
        ["product.chat_threads.company_id", "product.chat_threads.thread_id"],
        name="fk_chat_turns_thread",
    ),
)

chat_action_proposals = sa.Table(
    "chat_action_proposals",
    product_metadata,
    sa.Column("proposal_id", sa.Uuid(), nullable=False),
    sa.Column("thread_id", sa.Uuid(), nullable=False),
    sa.Column("turn_id", sa.Uuid(), nullable=False),
    sa.Column("company_id", sa.Text(), nullable=False),
    sa.Column("action_name", sa.String(64), nullable=False),
    sa.Column("title", sa.Text(), nullable=False),
    sa.Column("description", sa.Text(), nullable=False),
    sa.Column("state", sa.String(16), nullable=False),
    sa.Column("confirm_key_hash", sa.CHAR(64), nullable=True),
    sa.Column("command_id", sa.Uuid(), nullable=True),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint("proposal_id", name="pk_chat_action_proposals"),
    sa.UniqueConstraint("company_id", "proposal_id", name="uq_chat_action_proposals_company"),
    sa.UniqueConstraint("turn_id", name="uq_chat_action_proposals_turn"),
    sa.ForeignKeyConstraint(
        ["company_id", "turn_id"],
        ["product.chat_turns.company_id", "product.chat_turns.turn_id"],
        name="fk_chat_action_proposals_turn",
    ),
    sa.ForeignKeyConstraint(
        ["company_id", "thread_id"],
        ["product.chat_threads.company_id", "product.chat_threads.thread_id"],
        name="fk_chat_action_proposals_thread",
    ),
    sa.Index("ix_chat_action_proposals_thread", "company_id", "thread_id"),
)

_t = chat_threads.c
_u = chat_turns.c
_p = chat_action_proposals.c
_ERRORS = (sa_exc.SQLAlchemyError, OSError, ValidationError, KeyError, TypeError, ValueError)


def _thread(row: sa.RowMapping) -> ChatThread:
    return ChatThread.model_validate(dict(row))


def _turn(row: sa.RowMapping) -> ChatTurn:
    return ChatTurn.model_validate(dict(row))


def _proposal(row: sa.RowMapping) -> TicketProposal:
    data = dict(row)
    if data.get("confirm_key_hash") is not None:
        data["confirm_key_hash"] = str(data["confirm_key_hash"]).strip()
    return TicketProposal.model_validate(data)


class PostgresEmployeeChatRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = session_factory

    async def _all(self, query: sa.Select[Any]) -> list[sa.RowMapping]:
        try:
            async with self._sessions() as session:
                return list((await session.execute(query)).mappings().all())
        except _ERRORS:
            raise ChatRepositoryError() from None

    # ----- threads ---------------------------------------------------------------------------

    async def create_thread(self, thread: ChatThread) -> None:
        try:
            async with self._sessions() as session, session.begin():
                await session.execute(
                    sa.insert(chat_threads).values(
                        thread_id=thread.thread_id,
                        company_id=thread.company_id,
                        actor_id=thread.actor_id,
                        actor_type=thread.actor_type,
                        store_id=thread.store_id,
                        agent_id=thread.agent_id,
                        created_at=thread.created_at,
                        updated_at=thread.updated_at,
                        next_turn_sequence=thread.next_turn_sequence,
                    )
                )
        except _ERRORS:
            raise ChatRepositoryError() from None

    async def list_threads(
        self, company_id: str, actor_id: str, actor_type: str, store_id: str, limit: int
    ) -> tuple[ChatThread, ...]:
        rows = await self._all(
            sa.select(chat_threads)
            .where(
                _t.company_id == company_id,
                _t.actor_id == actor_id,
                _t.actor_type == actor_type,
                _t.store_id == store_id,
            )
            .order_by(_t.updated_at.desc(), _t.thread_id)
            .limit(limit)
        )
        try:
            return tuple(_thread(r) for r in rows)
        except _ERRORS:
            raise ChatRepositoryError() from None

    async def get_thread(
        self, company_id: str, actor_id: str, actor_type: str, thread_id: UUID
    ) -> ChatThread | None:
        rows = await self._all(
            sa.select(chat_threads).where(
                _t.company_id == company_id,
                _t.actor_id == actor_id,
                _t.actor_type == actor_type,
                _t.thread_id == thread_id,
            )
        )
        try:
            return _thread(rows[0]) if rows else None
        except _ERRORS:
            raise ChatRepositoryError() from None

    # ----- turns -----------------------------------------------------------------------------

    async def list_turns(
        self, company_id: str, thread_id: UUID, limit: int
    ) -> tuple[ChatTurn, ...]:
        rows = await self._all(
            sa.select(chat_turns)
            .where(_u.company_id == company_id, _u.thread_id == thread_id)
            .order_by(_u.sequence.desc())
            .limit(limit)
        )
        try:
            return tuple(sorted((_turn(r) for r in rows), key=lambda t: t.sequence))
        except _ERRORS:
            raise ChatRepositoryError() from None

    async def get_turn(self, company_id: str, turn_id: UUID) -> ChatTurn | None:
        rows = await self._all(
            sa.select(chat_turns).where(_u.company_id == company_id, _u.turn_id == turn_id)
        )
        try:
            return _turn(rows[0]) if rows else None
        except _ERRORS:
            raise ChatRepositoryError() from None

    async def begin_turn(
        self, company_id: str, thread_id: UUID, turn_id: UUID, user_text: str, now: datetime
    ) -> BeginTurnResult:
        try:
            return await self._begin(company_id, thread_id, turn_id, user_text, now)
        except sa_exc.IntegrityError:
            # A concurrent submission with the same turn id committed first.
            existing = await self._any_turn(turn_id)
            if existing is None:
                raise ChatRepositoryError() from None
            return BeginTurnResult(turn=existing, created=False)
        except _ERRORS:
            raise ChatRepositoryError() from None

    async def _any_turn(self, turn_id: UUID) -> ChatTurn | None:
        rows = await self._all(sa.select(chat_turns).where(_u.turn_id == turn_id))
        try:
            return _turn(rows[0]) if rows else None
        except _ERRORS:
            raise ChatRepositoryError() from None

    async def _begin(
        self, company_id: str, thread_id: UUID, turn_id: UUID, user_text: str, now: datetime
    ) -> BeginTurnResult:
        async with self._sessions() as session, session.begin():
            existing = (
                (await session.execute(sa.select(chat_turns).where(_u.turn_id == turn_id)))
                .mappings()
                .first()
            )
            if existing is not None:
                return BeginTurnResult(turn=_turn(existing), created=False)
            thread = (
                (
                    await session.execute(
                        sa.select(chat_threads)
                        .where(
                            _t.company_id == company_id,
                            _t.thread_id == thread_id,
                        )
                        .with_for_update()
                    )
                )
                .mappings()
                .one()
            )
            sequence = thread["next_turn_sequence"]
            turn = ChatTurn(
                turn_id=turn_id,
                thread_id=thread_id,
                company_id=company_id,
                sequence=sequence,
                user_text=user_text,
                assistant_text=None,
                status=TurnStatus.PENDING,
                failure=None,
                created_at=now,
                completed_at=None,
            )
            await session.execute(sa.insert(chat_turns).values(turn.model_dump()))
            await session.execute(
                sa.update(chat_threads)
                .where(_t.company_id == company_id, _t.thread_id == thread_id)
                .values(
                    next_turn_sequence=sequence + 1, updated_at=sa.func.greatest(_t.updated_at, now)
                )
            )
            return BeginTurnResult(turn=turn, created=True)

    async def history(
        self, company_id: str, thread_id: UUID, before_sequence: int, max_turns: int
    ) -> tuple[ChatTurn, ...]:
        rows = await self._all(
            sa.select(chat_turns)
            .where(
                _u.company_id == company_id,
                _u.thread_id == thread_id,
                _u.sequence < before_sequence,
                _u.status == TurnStatus.COMPLETED.value,
            )
            .order_by(_u.sequence.desc())
            .limit(max_turns)
        )
        try:
            return tuple(sorted((_turn(r) for r in rows), key=lambda t: t.sequence))
        except _ERRORS:
            raise ChatRepositoryError() from None

    async def complete_turn(
        self,
        company_id: str,
        turn_id: UUID,
        assistant_text: str,
        proposal: TicketProposal | None,
        now: datetime,
    ) -> tuple[ChatTurn, TicketProposal | None]:
        try:
            async with self._sessions() as session, session.begin():
                row = (
                    (
                        await session.execute(
                            sa.update(chat_turns)
                            .where(
                                _u.company_id == company_id,
                                _u.turn_id == turn_id,
                                _u.status == TurnStatus.PENDING.value,
                            )
                            .values(
                                status=TurnStatus.COMPLETED.value,
                                assistant_text=assistant_text,
                                completed_at=now,
                            )
                            .returning(*chat_turns.c)
                        )
                    )
                    .mappings()
                    .one()
                )
                if proposal is not None:
                    await session.execute(
                        sa.insert(chat_action_proposals).values(proposal.model_dump())
                    )
                return _turn(row), proposal
        except _ERRORS:
            raise ChatRepositoryError() from None

    async def fail_turn(
        self, company_id: str, turn_id: UUID, failure: TurnFailure, now: datetime
    ) -> ChatTurn:
        try:
            async with self._sessions() as session, session.begin():
                row = (
                    (
                        await session.execute(
                            sa.update(chat_turns)
                            .where(
                                _u.company_id == company_id,
                                _u.turn_id == turn_id,
                                _u.status == TurnStatus.PENDING.value,
                            )
                            .values(
                                status=TurnStatus.FAILED.value,
                                failure=failure.value,
                                completed_at=now,
                            )
                            .returning(*chat_turns.c)
                        )
                    )
                    .mappings()
                    .one()
                )
                return _turn(row)
        except _ERRORS:
            raise ChatRepositoryError() from None

    # ----- proposals -------------------------------------------------------------------------

    async def list_proposals(self, company_id: str, thread_id: UUID) -> tuple[TicketProposal, ...]:
        rows = await self._all(
            sa.select(chat_action_proposals)
            .where(_p.company_id == company_id, _p.thread_id == thread_id)
            .order_by(_p.created_at, _p.proposal_id)
        )
        try:
            return tuple(_proposal(r) for r in rows)
        except _ERRORS:
            raise ChatRepositoryError() from None

    async def get_proposal(self, company_id: str, proposal_id: UUID) -> TicketProposal | None:
        rows = await self._all(
            sa.select(chat_action_proposals).where(
                _p.company_id == company_id, _p.proposal_id == proposal_id
            )
        )
        try:
            return _proposal(rows[0]) if rows else None
        except _ERRORS:
            raise ChatRepositoryError() from None

    async def proposal_for_turn(self, company_id: str, turn_id: UUID) -> TicketProposal | None:
        rows = await self._all(
            sa.select(chat_action_proposals).where(
                _p.company_id == company_id, _p.turn_id == turn_id
            )
        )
        try:
            return _proposal(rows[0]) if rows else None
        except _ERRORS:
            raise ChatRepositoryError() from None

    async def claim_proposal(
        self, company_id: str, proposal_id: UUID, key_hash: str, now: datetime
    ) -> tuple[ClaimOutcome, TicketProposal | None]:
        try:
            async with self._sessions() as session, session.begin():
                claimed = (
                    (
                        await session.execute(
                            sa.update(chat_action_proposals)
                            .where(
                                _p.company_id == company_id,
                                _p.proposal_id == proposal_id,
                                _p.state == ProposalState.PROPOSED.value,
                            )
                            .values(
                                state=ProposalState.CONFIRMING.value,
                                confirm_key_hash=key_hash,
                                updated_at=now,
                            )
                            .returning(*chat_action_proposals.c)
                        )
                    )
                    .mappings()
                    .first()
                )
                if claimed is not None:
                    return ClaimOutcome.CLAIMED, _proposal(claimed)
                current = (
                    (
                        await session.execute(
                            sa.select(chat_action_proposals).where(
                                _p.company_id == company_id,
                                _p.proposal_id == proposal_id,
                            )
                        )
                    )
                    .mappings()
                    .first()
                )
        except _ERRORS:
            raise ChatRepositoryError() from None
        if current is None:
            return ClaimOutcome.NOT_FOUND, None
        proposal = _proposal(current)
        if proposal.state is ProposalState.CANCELLED:
            return ClaimOutcome.CANCELLED, proposal
        if proposal.confirm_key_hash == key_hash:
            return ClaimOutcome.SAME_KEY, proposal
        return ClaimOutcome.OTHER_KEY, proposal

    async def release_claim(
        self, company_id: str, proposal_id: UUID, key_hash: str, now: datetime
    ) -> None:
        try:
            async with self._sessions() as session, session.begin():
                await session.execute(
                    sa.update(chat_action_proposals)
                    .where(
                        _p.company_id == company_id,
                        _p.proposal_id == proposal_id,
                        _p.state == ProposalState.CONFIRMING.value,
                        _p.confirm_key_hash == key_hash,
                        _p.command_id.is_(None),
                    )
                    .values(
                        state=ProposalState.PROPOSED.value, confirm_key_hash=None, updated_at=now
                    )
                )
        except _ERRORS:
            raise ChatRepositoryError() from None

    async def record_submission(
        self, company_id: str, proposal_id: UUID, key_hash: str, command_id: UUID, now: datetime
    ) -> TicketProposal:
        try:
            async with self._sessions() as session, session.begin():
                row = (
                    (
                        await session.execute(
                            sa.update(chat_action_proposals)
                            .where(
                                _p.company_id == company_id,
                                _p.proposal_id == proposal_id,
                                _p.confirm_key_hash == key_hash,
                                _p.state.in_(
                                    (ProposalState.CONFIRMING.value, ProposalState.SUBMITTED.value)
                                ),
                                sa.or_(_p.command_id.is_(None), _p.command_id == command_id),
                            )
                            .values(
                                state=ProposalState.SUBMITTED.value,
                                command_id=command_id,
                                updated_at=now,
                            )
                            .returning(*chat_action_proposals.c)
                        )
                    )
                    .mappings()
                    .one()
                )
                return _proposal(row)
        except _ERRORS:
            raise ChatRepositoryError() from None

    async def cancel_proposal(
        self, company_id: str, proposal_id: UUID, now: datetime
    ) -> tuple[CancelOutcome, TicketProposal | None]:
        try:
            async with self._sessions() as session, session.begin():
                cancelled = (
                    (
                        await session.execute(
                            sa.update(chat_action_proposals)
                            .where(
                                _p.company_id == company_id,
                                _p.proposal_id == proposal_id,
                                _p.state == ProposalState.PROPOSED.value,
                            )
                            .values(state=ProposalState.CANCELLED.value, updated_at=now)
                            .returning(*chat_action_proposals.c)
                        )
                    )
                    .mappings()
                    .first()
                )
                if cancelled is not None:
                    return CancelOutcome.CANCELLED, _proposal(cancelled)
                current = (
                    (
                        await session.execute(
                            sa.select(chat_action_proposals).where(
                                _p.company_id == company_id,
                                _p.proposal_id == proposal_id,
                            )
                        )
                    )
                    .mappings()
                    .first()
                )
        except _ERRORS:
            raise ChatRepositoryError() from None
        if current is None:
            return CancelOutcome.NOT_FOUND, None
        proposal = _proposal(current)
        if proposal.state is ProposalState.CANCELLED:
            return CancelOutcome.ALREADY_CANCELLED, proposal
        return CancelOutcome.CLAIMED, proposal
