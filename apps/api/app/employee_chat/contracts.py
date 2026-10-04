"""Employee Chat contracts: the repository and the Operations chat runner.

The Product HTTP route and ``EmployeeChatService`` depend only on these Protocols: never
on Agno, the Operations Agent, persistence or an integration.
"""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol, runtime_checkable
from uuid import UUID

from app.context.models import RequestContext
from app.employee_chat.models import (
    ChatRunResult,
    ChatThread,
    ChatTurn,
    HistoryTurn,
    TicketProposal,
    TurnFailure,
)
from app.governance import ActionScope


@runtime_checkable
class OperationsChatRunService(Protocol):
    """The Product-owned Operations chat runner (implemented around the Operations Agent).

    ``request`` and ``scope`` are trusted (store scoped). ``message`` and ``history`` are
    untrusted conversation text: they cannot change identity, scope, permissions or
    policy. Implementations never execute a business write: a ticket can only come back
    as a typed ``ProposedTicket``."""

    async def ensure_runnable(self, scope: ActionScope) -> None:
        """Raise ``OperationsAgentDisabledError`` (or another error) before anything is
        recorded or run, when the Operations Agent cannot run in this scope."""
        ...

    async def run_chat(
        self,
        request: RequestContext,
        scope: ActionScope,
        message: str,
        history: tuple[HistoryTurn, ...],
    ) -> ChatRunResult: ...


@dataclass(frozen=True)
class BeginTurnResult:
    turn: ChatTurn
    created: bool


class ClaimOutcome(StrEnum):
    CLAIMED = "claimed"  # proposed -> confirming with this key
    SAME_KEY = "same_key"  # already claimed or submitted with this key: an idempotent retry
    OTHER_KEY = "other_key"  # claimed or submitted with another key: refused
    CANCELLED = "cancelled"
    NOT_FOUND = "not_found"


class CancelOutcome(StrEnum):
    CANCELLED = "cancelled"
    ALREADY_CANCELLED = "already_cancelled"
    CLAIMED = "claimed"  # confirming or submitted: no longer cancellable
    NOT_FOUND = "not_found"


@runtime_checkable
class EmployeeChatRepository(Protocol):
    """Every method carries the trusted ``company_id`` (and, for threads, the actor) in
    its query. Failures raise ``ChatRepositoryError``."""

    async def create_thread(self, thread: ChatThread) -> None: ...

    async def list_threads(
        self, company_id: str, actor_id: str, actor_type: str, store_id: str, limit: int
    ) -> tuple[ChatThread, ...]: ...

    async def get_thread(
        self, company_id: str, actor_id: str, actor_type: str, thread_id: UUID
    ) -> ChatThread | None: ...

    async def list_turns(
        self, company_id: str, thread_id: UUID, limit: int
    ) -> tuple[ChatTurn, ...]: ...

    async def list_proposals(
        self, company_id: str, thread_id: UUID
    ) -> tuple[TicketProposal, ...]: ...

    async def get_turn(self, company_id: str, turn_id: UUID) -> ChatTurn | None: ...

    async def begin_turn(
        self, company_id: str, thread_id: UUID, turn_id: UUID, user_text: str, now: datetime
    ) -> BeginTurnResult:
        """Atomically return the existing turn with this id (``created`` False), or lock
        the thread, take its next sequence and record a PENDING turn."""
        ...

    async def history(
        self, company_id: str, thread_id: UUID, before_sequence: int, max_turns: int
    ) -> tuple[ChatTurn, ...]:
        """The most recent COMPLETED turns before ``before_sequence``, ascending."""
        ...

    async def complete_turn(
        self,
        company_id: str,
        turn_id: UUID,
        assistant_text: str,
        proposal: TicketProposal | None,
        now: datetime,
    ) -> tuple[ChatTurn, TicketProposal | None]:
        """PENDING -> COMPLETED and the turn's proposal, in ONE transaction."""
        ...

    async def fail_turn(
        self, company_id: str, turn_id: UUID, failure: TurnFailure, now: datetime
    ) -> ChatTurn: ...

    async def get_proposal(self, company_id: str, proposal_id: UUID) -> TicketProposal | None: ...

    async def proposal_for_turn(self, company_id: str, turn_id: UUID) -> TicketProposal | None: ...

    async def claim_proposal(
        self, company_id: str, proposal_id: UUID, key_hash: str, now: datetime
    ) -> tuple[ClaimOutcome, TicketProposal | None]:
        """Compare-and-set PROPOSED -> CONFIRMING with this key hash (one winner)."""
        ...

    async def release_claim(
        self, company_id: str, proposal_id: UUID, key_hash: str, now: datetime
    ) -> None:
        """CONFIRMING (with this key, no command) -> PROPOSED again."""
        ...

    async def record_submission(
        self, company_id: str, proposal_id: UUID, key_hash: str, command_id: UUID, now: datetime
    ) -> TicketProposal:
        """CONFIRMING/SUBMITTED with this key -> SUBMITTED with the durable command id."""
        ...

    async def cancel_proposal(
        self, company_id: str, proposal_id: UUID, now: datetime
    ) -> tuple[CancelOutcome, TicketProposal | None]:
        """Compare-and-set PROPOSED -> CANCELLED (one winner against a confirmation)."""
        ...
