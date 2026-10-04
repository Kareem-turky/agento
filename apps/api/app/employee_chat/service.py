"""``EmployeeChatService``: the Product-owned Employee Chat boundary (Task 042).

    Chat HTTP route -> EmployeeChatService -> OperationsChatRunService (Agent-gated)
                                           -> EmployeeChatRepository (PostgreSQL)
                                           -> OperationsTicketCommandService (confirmation)

Identity and scope come ONLY from the authenticated ``ActorContext``:
- a thread belongs to company + actor (id AND type) + store, with ``agent_id`` fixed to
  ``operations``;
- every read, turn and confirmation re-checks that the thread's store is CURRENTLY
  granted. Another actor's or company's thread, an unknown one, or one whose store is
  no longer granted is the same ``ChatNotFoundError`` (404).

A turn is idempotent by its client-generated ``turn_id``: a completed replay returns the
stored answer without calling the model again; the same id with a different message is
a conflict; a pending duplicate is ``ChatTurnInProgressError``. The Agent gate is
checked BEFORE anything is recorded or run.

The model may only PROPOSE ``operations.ticket.create`` (a typed proposal, stored with
the completed turn). Nothing is executed until a separate, explicit human confirmation:
the stored proposal (never client-resent text) goes through the EXISTING ticket
``WriteCommand`` service (permission, policy, execution, verification, audit), at most
once per proposal: a compare-and-set claim binds it to ONE Idempotency-Key, an
ambiguous retry with the same key replays the durable command, another key is refused,
and a cancelled proposal never executes.
"""

import hashlib
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4

from app.context.models import ActorContext, RequestContext
from app.employee_chat.contracts import (
    CancelOutcome,
    ClaimOutcome,
    EmployeeChatRepository,
    OperationsChatRunService,
)
from app.employee_chat.errors import (
    ChatAgentDisabledError,
    ChatForbiddenError,
    ChatIdempotencyConflictError,
    ChatInvalidIdempotencyKeyError,
    ChatNotFoundError,
    ChatProposalAlreadyConfirmedError,
    ChatProposalCancelledError,
    ChatProposalNotFoundError,
    ChatRepositoryError,
    ChatTurnConflictError,
    ChatTurnInProgressError,
    ChatUnavailableError,
    EmployeeChatError,
)
from app.employee_chat.history import bounded_history
from app.employee_chat.models import (
    CHAT_AGENT_ID,
    HISTORY_MAX_TURNS,
    MAX_ASSISTANT_CHARS,
    MAX_THREADS_LISTED,
    MAX_TURNS_LISTED,
    TICKET_PROPOSAL_ACTION,
    ChatRunResult,
    ChatThread,
    ChatTurn,
    ProposalState,
    TicketProposal,
    TurnFailure,
    TurnStatus,
    utc_now,
)
from app.governance import ActionScope
from app.observability.contracts import (
    BusinessDetails,
    ObservationDetails,
    ObservationOutcome,
    ProductObservability,
    ProductOperation,
    observe,
)
from app.services.operations import OperationsAgentDisabledError
from app.services.operations_tickets import (
    IdempotencyConflictError,
    InvalidIdempotencyKeyError,
    OperationsTicketCommandService,
    ProductTicketCommandResult,
)

# The SAME opaque-key rule as the WriteCommand layer (``app.commands.IDEMPOTENCY_KEY_PATTERN``,
# equality pinned by a test). Kept local so the HTTP boot never loads the command layer.
CONFIRM_KEY_PATTERN = r"^[A-Za-z0-9._:~-]{1,128}$"
_KEY = re.compile(CONFIRM_KEY_PATTERN)


def _key_hash(key: str) -> str:
    # SHA-256 hex of the exact key, like ``app.commands.hash_idempotency_key``.
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


class ChatThreadEvent(StrEnum):
    CREATE = "create"
    LIST = "list"
    READ = "read"


class ChatTurnEvent(StrEnum):
    COMPLETED = "completed"
    REPLAYED = "replayed"
    FAILED = "failed"
    REFUSED = "refused"


class ChatProposalEvent(StrEnum):
    PROPOSED = "proposed"
    CONFIRMED = "confirmed"
    CANCELLED = "cancelled"
    REFUSED = "refused"


@dataclass(frozen=True)
class ThreadView:
    thread: ChatThread
    turns: tuple[ChatTurn, ...]
    proposals: tuple[TicketProposal, ...]


@dataclass(frozen=True)
class TurnOutcome:
    turn: ChatTurn
    proposal: TicketProposal | None
    replayed: bool


@dataclass(frozen=True)
class ConfirmOutcome:
    proposal: TicketProposal
    ticket: ProductTicketCommandResult


def _outcome(error: EmployeeChatError) -> ObservationOutcome:
    if isinstance(error, ChatForbiddenError):
        return ObservationOutcome.DENIED
    if isinstance(error, ChatNotFoundError | ChatProposalNotFoundError):
        return ObservationOutcome.NOT_FOUND
    if isinstance(error, ChatInvalidIdempotencyKeyError):
        return ObservationOutcome.INVALID
    if isinstance(error, ChatUnavailableError):
        return ObservationOutcome.UNAVAILABLE
    return ObservationOutcome.CONFLICT


def _details(status: StrEnum, *, replayed: bool | None = None) -> ObservationDetails:
    return ObservationDetails(business=BusinessDetails(status=status, replayed=replayed))


class EmployeeChatService:
    def __init__(
        self,
        repository: EmployeeChatRepository,
        runner: OperationsChatRunService | None,
        tickets: OperationsTicketCommandService | None,
        *,
        observability: ProductObservability | None = None,
        clock: Callable[[], datetime] = utc_now,
        new_id: Callable[[], UUID] = uuid4,
    ) -> None:
        self._repository = repository
        self._runner = runner
        self._tickets = tickets
        self._observability = observability
        self._clock = clock
        self._new_id = new_id

    # ----- identity and ownership ------------------------------------------------------------

    @staticmethod
    def _actor(context: RequestContext) -> ActorContext:
        if not isinstance(context, RequestContext) or context.actor is None:
            raise ChatNotFoundError()
        return context.actor

    async def _owned(self, actor: ActorContext, thread_id: UUID) -> ChatThread:
        try:
            thread = await self._repository.get_thread(actor.company_id, actor.actor_id,
                                                       actor.actor_type, thread_id)  # fmt: skip
        except ChatRepositoryError:
            raise ChatUnavailableError() from None
        # The thread's store must be granted NOW: revoking a store hides its threads.
        if thread is None or thread.store_id not in actor.store_ids:
            raise ChatNotFoundError()
        return thread

    @staticmethod
    def _scope(thread: ChatThread) -> ActionScope:
        return ActionScope(company_id=thread.company_id, store_id=thread.store_id)

    # ----- threads ---------------------------------------------------------------------------

    async def create_thread(self, context: RequestContext, store_id: str) -> ChatThread:
        with observe(self._observability, ProductOperation.CHAT_THREAD,
                     context.request_id) as obs:  # fmt: skip
            try:
                actor = self._actor(context)
                if store_id not in actor.store_ids:
                    raise ChatForbiddenError()
                now = self._clock()
                thread = ChatThread(
                    thread_id=self._new_id(), company_id=actor.company_id,
                    actor_id=actor.actor_id, actor_type=actor.actor_type, store_id=store_id,
                    agent_id=CHAT_AGENT_ID, created_at=now, updated_at=now,
                    next_turn_sequence=1,
                )  # fmt: skip
                try:
                    await self._repository.create_thread(thread)
                except ChatRepositoryError:
                    raise ChatUnavailableError() from None
            except EmployeeChatError as error:
                obs.finish(_outcome(error), _details(ChatThreadEvent.CREATE))
                raise
            obs.finish(ObservationOutcome.COMPLETED, _details(ChatThreadEvent.CREATE))
            return thread

    async def list_threads(self, context: RequestContext,
                           store_id: str) -> tuple[ChatThread, ...]:  # fmt: skip
        with observe(self._observability, ProductOperation.CHAT_THREAD,
                     context.request_id) as obs:  # fmt: skip
            try:
                actor = self._actor(context)
                if store_id not in actor.store_ids:
                    raise ChatForbiddenError()
                try:
                    threads = await self._repository.list_threads(
                        actor.company_id, actor.actor_id, actor.actor_type, store_id,
                        MAX_THREADS_LISTED)  # fmt: skip
                except ChatRepositoryError:
                    raise ChatUnavailableError() from None
            except EmployeeChatError as error:
                obs.finish(_outcome(error), _details(ChatThreadEvent.LIST))
                raise
            obs.finish(ObservationOutcome.COMPLETED, _details(ChatThreadEvent.LIST))
            return threads

    async def get_thread(self, context: RequestContext, thread_id: UUID) -> ThreadView:
        with observe(self._observability, ProductOperation.CHAT_THREAD,
                     context.request_id) as obs:  # fmt: skip
            try:
                actor = self._actor(context)
                thread = await self._owned(actor, thread_id)
                try:
                    turns = await self._repository.list_turns(actor.company_id, thread_id,
                                                              MAX_TURNS_LISTED)  # fmt: skip
                    proposals = await self._repository.list_proposals(actor.company_id,
                                                                      thread_id)  # fmt: skip
                except ChatRepositoryError:
                    raise ChatUnavailableError() from None
            except EmployeeChatError as error:
                obs.finish(_outcome(error), _details(ChatThreadEvent.READ))
                raise
            obs.finish(ObservationOutcome.COMPLETED, _details(ChatThreadEvent.READ))
            return ThreadView(thread=thread, turns=turns, proposals=proposals)

    # ----- turns -----------------------------------------------------------------------------

    async def submit_turn(self, context: RequestContext, thread_id: UUID, turn_id: UUID,
                          message: str) -> TurnOutcome:  # fmt: skip
        with observe(self._observability, ProductOperation.CHAT_TURN,
                     context.request_id) as obs:  # fmt: skip
            try:
                outcome = await self._submit(context, thread_id, turn_id, message)
            except EmployeeChatError as error:
                event = (ChatTurnEvent.FAILED if isinstance(error, ChatUnavailableError)
                         else ChatTurnEvent.REFUSED)  # fmt: skip
                obs.finish(_outcome(error), _details(event))
                raise
            if outcome.replayed:
                obs.finish(ObservationOutcome.COMPLETED,
                           _details(ChatTurnEvent.REPLAYED, replayed=True))  # fmt: skip
            elif outcome.turn.status is TurnStatus.COMPLETED:
                obs.finish(ObservationOutcome.COMPLETED,
                           _details(ChatTurnEvent.COMPLETED, replayed=False))  # fmt: skip
            else:
                obs.finish(ObservationOutcome.ERROR, _details(ChatTurnEvent.FAILED))
        if outcome.proposal is not None and not outcome.replayed:
            with observe(self._observability, ProductOperation.CHAT_TICKET_PROPOSAL,
                         context.request_id) as proposal_obs:  # fmt: skip
                proposal_obs.finish(ObservationOutcome.COMPLETED,
                                    _details(ChatProposalEvent.PROPOSED))  # fmt: skip
        return outcome

    async def _replay(self, existing: ChatTurn, thread: ChatThread, message: str) -> TurnOutcome:
        if existing.thread_id != thread.thread_id or existing.user_text != message:
            raise ChatTurnConflictError()
        if existing.status is TurnStatus.PENDING:
            raise ChatTurnInProgressError()
        try:
            proposal = await self._repository.proposal_for_turn(thread.company_id,
                                                                existing.turn_id)  # fmt: skip
        except ChatRepositoryError:
            raise ChatUnavailableError() from None
        return TurnOutcome(turn=existing, proposal=proposal, replayed=True)

    async def _submit(self, context: RequestContext, thread_id: UUID, turn_id: UUID,
                      message: str) -> TurnOutcome:  # fmt: skip
        actor = self._actor(context)
        thread = await self._owned(actor, thread_id)
        try:
            existing = await self._repository.get_turn(actor.company_id, turn_id)
        except ChatRepositoryError:
            raise ChatUnavailableError() from None
        if existing is not None:
            return await self._replay(existing, thread, message)
        runner = self._runner
        if runner is None:
            raise ChatUnavailableError()
        scope = self._scope(thread)
        # The Agent gate BEFORE anything is recorded or run: a disabled Agent leaves no
        # turn, no model call, no tool call and no proposal.
        try:
            await runner.ensure_runnable(scope)
        except OperationsAgentDisabledError:
            raise ChatAgentDisabledError() from None
        except Exception:  # noqa: BLE001 - not runnable for any other reason
            raise ChatUnavailableError() from None
        try:
            begun = await self._repository.begin_turn(actor.company_id, thread_id, turn_id,
                                                      message, self._clock())  # fmt: skip
        except ChatRepositoryError:
            raise ChatUnavailableError() from None
        if not begun.created:  # a concurrent identical submission won the insert
            return await self._replay(begun.turn, thread, message)
        try:
            prior = await self._repository.history(
                actor.company_id, thread_id, begun.turn.sequence, HISTORY_MAX_TURNS
            )
        except ChatRepositoryError:
            await self._fail(actor, turn_id, TurnFailure.RUN_FAILED)
            raise ChatUnavailableError() from None
        try:
            result = await runner.run_chat(context, scope, message, bounded_history(prior))
            if not isinstance(result, ChatRunResult):
                raise TypeError("invalid chat runner result")
        except OperationsAgentDisabledError:  # disabled while the turn was being recorded
            await self._fail(actor, turn_id, TurnFailure.AGENT_DISABLED)
            raise ChatAgentDisabledError() from None
        except Exception:  # noqa: BLE001 - never surface model, provider or tool detail
            await self._fail(actor, turn_id, TurnFailure.RUN_FAILED)
            raise ChatUnavailableError() from None
        now = self._clock()
        proposal = None
        if result.proposal is not None:
            proposal = TicketProposal(
                proposal_id=self._new_id(), thread_id=thread_id, turn_id=turn_id,
                company_id=actor.company_id, action_name=TICKET_PROPOSAL_ACTION,
                title=result.proposal.title, description=result.proposal.description,
                state=ProposalState.PROPOSED, confirm_key_hash=None, command_id=None,
                created_at=now, updated_at=now,
            )  # fmt: skip
        try:
            turn, stored = await self._repository.complete_turn(
                actor.company_id, turn_id, result.message[:MAX_ASSISTANT_CHARS], proposal, now
            )
        except ChatRepositoryError:
            raise ChatUnavailableError() from None
        return TurnOutcome(turn=turn, proposal=stored, replayed=False)

    async def _fail(self, actor: ActorContext, turn_id: UUID, failure: TurnFailure) -> None:
        try:
            await self._repository.fail_turn(actor.company_id, turn_id, failure, self._clock())
        except ChatRepositoryError:
            pass  # the turn stays pending; a replay reports "in progress", never an answer

    # ----- ticket proposals ------------------------------------------------------------------

    async def _owned_proposal(self, actor: ActorContext,
                              proposal_id: UUID) -> tuple[TicketProposal, ChatThread]:  # fmt: skip
        try:
            proposal = await self._repository.get_proposal(actor.company_id, proposal_id)
        except ChatRepositoryError:
            raise ChatUnavailableError() from None
        if proposal is None:
            raise ChatProposalNotFoundError()
        try:
            thread = await self._owned(actor, proposal.thread_id)
        except ChatNotFoundError:
            raise ChatProposalNotFoundError() from None
        return proposal, thread

    async def confirm_ticket(self, context: RequestContext, proposal_id: UUID,
                             idempotency_key: str) -> ConfirmOutcome:  # fmt: skip
        with observe(self._observability, ProductOperation.CHAT_TICKET_PROPOSAL,
                     context.request_id) as obs:  # fmt: skip
            try:
                outcome = await self._confirm(context, proposal_id, idempotency_key)
            except EmployeeChatError as error:
                obs.finish(_outcome(error), _details(ChatProposalEvent.REFUSED))
                raise
            obs.finish(ObservationOutcome.COMPLETED,
                       _details(ChatProposalEvent.CONFIRMED,
                                replayed=outcome.ticket.replayed))  # fmt: skip
            return outcome

    async def _confirm(self, context: RequestContext, proposal_id: UUID,
                       idempotency_key: str) -> ConfirmOutcome:  # fmt: skip
        actor = self._actor(context)
        proposal, thread = await self._owned_proposal(actor, proposal_id)
        if not isinstance(idempotency_key, str) or not _KEY.fullmatch(idempotency_key):
            raise ChatInvalidIdempotencyKeyError()
        tickets = self._tickets
        if tickets is None:
            raise ChatUnavailableError()
        key_hash = _key_hash(idempotency_key)
        try:
            claim, claimed = await self._repository.claim_proposal(
                actor.company_id, proposal_id, key_hash, self._clock()
            )
        except ChatRepositoryError:
            raise ChatUnavailableError() from None
        if claim is ClaimOutcome.NOT_FOUND or claimed is None:
            raise ChatProposalNotFoundError()
        if claim is ClaimOutcome.CANCELLED:
            raise ChatProposalCancelledError()
        if claim is ClaimOutcome.OTHER_KEY:
            raise ChatProposalAlreadyConfirmedError()
        # CLAIMED or SAME_KEY: the STORED proposal (never client text) through the existing
        # governed ticket WriteCommand. The command layer replays a same-key retry.
        try:
            result = await tickets.create_ticket(context, self._scope(thread), claimed.title,
                                                 claimed.description, idempotency_key)  # fmt: skip
            if not isinstance(result, ProductTicketCommandResult):
                raise TypeError("invalid ticket service result")
        except IdempotencyConflictError:
            # The key belongs to ANOTHER request: nothing ran for this proposal.
            await self._release(actor, proposal_id, key_hash, claim)
            raise ChatIdempotencyConflictError() from None
        except InvalidIdempotencyKeyError:
            await self._release(actor, proposal_id, key_hash, claim)
            raise ChatInvalidIdempotencyKeyError() from None
        except Exception:  # noqa: BLE001 - outcome unknown: keep the claim for a same-key retry
            raise ChatUnavailableError() from None
        try:
            submitted = await self._repository.record_submission(
                actor.company_id, proposal_id, key_hash, result.command_id, self._clock()
            )
        except ChatRepositoryError:
            raise ChatUnavailableError() from None
        return ConfirmOutcome(proposal=submitted, ticket=result)

    async def _release(self, actor: ActorContext, proposal_id: UUID, key_hash: str,
                       claim: ClaimOutcome) -> None:  # fmt: skip
        if claim is not ClaimOutcome.CLAIMED:
            return
        try:
            await self._repository.release_claim(actor.company_id, proposal_id, key_hash,
                                                 self._clock())  # fmt: skip
        except ChatRepositoryError:
            pass

    async def cancel_ticket(self, context: RequestContext, proposal_id: UUID) -> TicketProposal:
        with observe(self._observability, ProductOperation.CHAT_TICKET_PROPOSAL,
                     context.request_id) as obs:  # fmt: skip
            try:
                actor = self._actor(context)
                await self._owned_proposal(actor, proposal_id)
                try:
                    outcome, proposal = await self._repository.cancel_proposal(
                        actor.company_id, proposal_id, self._clock()
                    )
                except ChatRepositoryError:
                    raise ChatUnavailableError() from None
                if outcome is CancelOutcome.NOT_FOUND or proposal is None:
                    raise ChatProposalNotFoundError()
                if outcome is CancelOutcome.CLAIMED:
                    raise ChatProposalAlreadyConfirmedError()
            except EmployeeChatError as error:
                obs.finish(_outcome(error), _details(ChatProposalEvent.REFUSED))
                raise
            obs.finish(ObservationOutcome.COMPLETED, _details(ChatProposalEvent.CANCELLED))
            return proposal
