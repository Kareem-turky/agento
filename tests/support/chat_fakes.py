"""TEST-ONLY Employee Chat fakes (Task 042): an in-memory repository with the SAME
semantics as ``PostgresEmployeeChatRepository`` (company scope, atomic begin, CAS claims),
a scripted chat runner and a recording ticket service. Never used by production code."""

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from app.context.models import ActorContext, RequestContext
from app.employee_chat.contracts import BeginTurnResult, CancelOutcome, ClaimOutcome
from app.employee_chat.errors import ChatRepositoryError
from app.employee_chat.models import (
    ChatRunResult,
    ChatThread,
    ChatTurn,
    HistoryTurn,
    ProposalState,
    ProposedTicket,
    TicketProposal,
    TurnFailure,
    TurnStatus,
)
from app.services.operations import OperationsAgentDisabledError
from app.services.operations_tickets import (
    IdempotencyConflictError,
    ProductTicketCommandResult,
    TicketCommandReason,
    TicketCommandStatus,
)

COMPANY = "chat-company"
STORE = "22222222-2222-4222-8222-222222222222"
OTHER_STORE = "33333333-3333-4333-8333-333333333333"


def context(
    actor_id: str = "employee",
    *,
    company: str = COMPANY,
    stores: frozenset[str] = frozenset({STORE}),
    permissions: frozenset[str] = frozenset({"tickets.create"}),
) -> RequestContext:
    return RequestContext(
        actor=ActorContext(
            actor_id=actor_id,
            actor_type="user",
            company_id=company,
            permissions=permissions,
            store_ids=stores,
        ),
        channel="api",
    )


class InMemoryChatRepository:
    def __init__(self) -> None:
        self.threads: dict[UUID, ChatThread] = {}
        self.turns: dict[UUID, ChatTurn] = {}
        self.proposals: dict[UUID, TicketProposal] = {}
        self.fail = False

    def _check(self) -> None:
        if self.fail:
            raise ChatRepositoryError()

    async def create_thread(self, thread: ChatThread) -> None:
        self._check()
        self.threads[thread.thread_id] = thread

    async def list_threads(
        self, company_id: str, actor_id: str, actor_type: str, store_id: str, limit: int
    ) -> tuple[ChatThread, ...]:
        self._check()
        found = [
            t
            for t in self.threads.values()
            if (t.company_id, t.actor_id, t.actor_type, t.store_id)
            == (company_id, actor_id, actor_type, store_id)
        ]
        return tuple(sorted(found, key=lambda t: t.updated_at, reverse=True)[:limit])

    async def get_thread(
        self, company_id: str, actor_id: str, actor_type: str, thread_id: UUID
    ) -> ChatThread | None:
        self._check()
        t = self.threads.get(thread_id)
        if t is None or (t.company_id, t.actor_id, t.actor_type) != (
            company_id,
            actor_id,
            actor_type,
        ):
            return None
        return t

    async def list_turns(
        self, company_id: str, thread_id: UUID, limit: int
    ) -> tuple[ChatTurn, ...]:
        self._check()
        found = sorted(
            (
                t
                for t in self.turns.values()
                if t.company_id == company_id and t.thread_id == thread_id
            ),
            key=lambda t: t.sequence,
        )
        return tuple(found[-limit:])

    async def list_proposals(self, company_id: str, thread_id: UUID) -> tuple[TicketProposal, ...]:
        self._check()
        return tuple(
            p
            for p in self.proposals.values()
            if p.company_id == company_id and p.thread_id == thread_id
        )

    async def get_turn(self, company_id: str, turn_id: UUID) -> ChatTurn | None:
        self._check()
        t = self.turns.get(turn_id)
        return t if t is not None and t.company_id == company_id else None

    async def begin_turn(
        self, company_id: str, thread_id: UUID, turn_id: UUID, user_text: str, now: datetime
    ) -> BeginTurnResult:
        self._check()
        if turn_id in self.turns:
            return BeginTurnResult(turn=self.turns[turn_id], created=False)
        thread = self.threads[thread_id]
        turn = ChatTurn(
            turn_id=turn_id,
            thread_id=thread_id,
            company_id=company_id,
            sequence=thread.next_turn_sequence,
            user_text=user_text,
            assistant_text=None,
            status=TurnStatus.PENDING,
            failure=None,
            created_at=now,
            completed_at=None,
        )
        self.turns[turn_id] = turn
        self.threads[thread_id] = thread.model_copy(
            update={
                "next_turn_sequence": thread.next_turn_sequence + 1,
                "updated_at": max(thread.updated_at, now),
            }
        )
        return BeginTurnResult(turn=turn, created=True)

    async def history(
        self, company_id: str, thread_id: UUID, before_sequence: int, max_turns: int
    ) -> tuple[ChatTurn, ...]:
        self._check()
        found = [
            t
            for t in await self.list_turns(company_id, thread_id, 10_000)
            if t.sequence < before_sequence and t.status is TurnStatus.COMPLETED
        ]
        return tuple(found[-max_turns:])

    async def complete_turn(
        self,
        company_id: str,
        turn_id: UUID,
        assistant_text: str,
        proposal: TicketProposal | None,
        now: datetime,
    ) -> tuple[ChatTurn, TicketProposal | None]:
        self._check()
        turn = self.turns[turn_id].model_copy(
            update={
                "status": TurnStatus.COMPLETED,
                "assistant_text": assistant_text,
                "completed_at": now,
            }
        )
        self.turns[turn_id] = turn
        if proposal is not None:
            self.proposals[proposal.proposal_id] = proposal
        return turn, proposal

    async def fail_turn(
        self, company_id: str, turn_id: UUID, failure: TurnFailure, now: datetime
    ) -> ChatTurn:
        self._check()
        turn = self.turns[turn_id].model_copy(
            update={"status": TurnStatus.FAILED, "failure": failure, "completed_at": now}
        )
        self.turns[turn_id] = turn
        return turn

    async def get_proposal(self, company_id: str, proposal_id: UUID) -> TicketProposal | None:
        self._check()
        p = self.proposals.get(proposal_id)
        return p if p is not None and p.company_id == company_id else None

    async def proposal_for_turn(self, company_id: str, turn_id: UUID) -> TicketProposal | None:
        self._check()
        return next(
            (
                p
                for p in self.proposals.values()
                if p.company_id == company_id and p.turn_id == turn_id
            ),
            None,
        )

    def _set(self, proposal: TicketProposal, **update: Any) -> TicketProposal:
        updated = TicketProposal.model_validate({**proposal.model_dump(), **update})
        self.proposals[proposal.proposal_id] = updated
        return updated

    async def claim_proposal(
        self, company_id: str, proposal_id: UUID, key_hash: str, now: datetime
    ) -> tuple[ClaimOutcome, TicketProposal | None]:
        self._check()
        p = await self.get_proposal(company_id, proposal_id)
        if p is None:
            return ClaimOutcome.NOT_FOUND, None
        if p.state is ProposalState.PROPOSED:
            return ClaimOutcome.CLAIMED, self._set(
                p, state=ProposalState.CONFIRMING, confirm_key_hash=key_hash, updated_at=now
            )
        if p.state is ProposalState.CANCELLED:
            return ClaimOutcome.CANCELLED, p
        if p.confirm_key_hash == key_hash:
            return ClaimOutcome.SAME_KEY, p
        return ClaimOutcome.OTHER_KEY, p

    async def release_claim(
        self, company_id: str, proposal_id: UUID, key_hash: str, now: datetime
    ) -> None:
        self._check()
        p = await self.get_proposal(company_id, proposal_id)
        if p is not None and p.state is ProposalState.CONFIRMING and p.confirm_key_hash == key_hash:
            self._set(p, state=ProposalState.PROPOSED, confirm_key_hash=None, updated_at=now)

    async def record_submission(
        self, company_id: str, proposal_id: UUID, key_hash: str, command_id: UUID, now: datetime
    ) -> TicketProposal:
        self._check()
        p = await self.get_proposal(company_id, proposal_id)
        if p is None or p.confirm_key_hash != key_hash:
            raise ChatRepositoryError()
        return self._set(p, state=ProposalState.SUBMITTED, command_id=command_id, updated_at=now)

    async def cancel_proposal(
        self, company_id: str, proposal_id: UUID, now: datetime
    ) -> tuple[CancelOutcome, TicketProposal | None]:
        self._check()
        p = await self.get_proposal(company_id, proposal_id)
        if p is None:
            return CancelOutcome.NOT_FOUND, None
        if p.state is ProposalState.PROPOSED:
            return CancelOutcome.CANCELLED, self._set(
                p, state=ProposalState.CANCELLED, updated_at=now
            )
        if p.state is ProposalState.CANCELLED:
            return CancelOutcome.ALREADY_CANCELLED, p
        return CancelOutcome.CLAIMED, p


class ScriptedChatRunner:
    """Answers with ``reply`` (and an optional proposal); records every run."""

    def __init__(self, reply: str = "answer", proposal: ProposedTicket | None = None) -> None:
        self.reply = reply
        self.proposal = proposal
        self.runs: list[tuple[str, tuple[HistoryTurn, ...]]] = []
        self.disabled = False
        self.disable_during_run = False
        self.error: Exception | None = None

    async def ensure_runnable(self, scope: Any) -> None:
        if self.disabled:
            raise OperationsAgentDisabledError()

    async def run_chat(
        self, request: Any, scope: Any, message: str, history: tuple[HistoryTurn, ...]
    ) -> ChatRunResult:
        if self.disable_during_run:
            raise OperationsAgentDisabledError()
        self.runs.append((message, history))
        if self.error is not None:
            raise self.error
        return ChatRunResult(message=self.reply, proposal=self.proposal)


class RecordingTickets:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.commands: dict[str, UUID] = {}
        self.error: Exception | None = None

    async def create_ticket(
        self, context: Any, scope: Any, title: str, description: str, key: str
    ) -> ProductTicketCommandResult:
        self.calls.append(
            {
                "scope": scope,
                "title": title,
                "description": description,
                "key": key,
                "actor": context.actor.actor_id,
            }
        )
        if self.error is not None:
            raise self.error
        if key == "conflicting-key":
            raise IdempotencyConflictError()
        replayed = key in self.commands
        command_id = self.commands.setdefault(key, uuid4())
        return ProductTicketCommandResult(
            command_id=command_id,
            status=TicketCommandStatus.VERIFIED,
            reason=TicketCommandReason.VERIFIED,
            ticket_id=uuid4(),
            replayed=replayed,
            persistence_complete=True,
        )


def revoke(context_: RequestContext, store: str = STORE) -> RequestContext:
    assert context_.actor is not None
    return context(
        context_.actor.actor_id,
        company=context_.actor.company_id,
        stores=context_.actor.store_ids - {store},
        permissions=context_.actor.permissions,
    )
