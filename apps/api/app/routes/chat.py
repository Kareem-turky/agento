"""Product Employee Chat API (Task 042): the employee's chat with the Operations Agent.

    GET  /api/v1/chat/threads?store_id=                    the actor's threads in a store
    POST /api/v1/chat/threads              {store_id}      a new, empty thread (no model call)
    GET  /api/v1/chat/thread?thread_id=                    one thread with turns + proposals
    GET  /api/v1/chat/turns?thread_id=                     the thread's turns + proposals
    POST /api/v1/chat/turns   {thread_id, turn_id, message}  one idempotent turn
    POST /api/v1/chat/ticket-proposals/confirm  {proposal_id} + exactly one Idempotency-Key
    POST /api/v1/chat/ticket-proposals/cancel   {proposal_id}

Paths are FIXED (ids are query parameters or body fields), so the AgentOS exemption stays
a list of exact paths. Product authentication only (the ``ActorResolver``), never AgentOS.
The route never imports Agno or an Agent: ``EmployeeChatService`` owns the boundary.

The client supplies only a store selector (checked against the actor's granted stores),
its own idempotent ``turn_id``, the message text, and for a confirmation the proposal id
and one Idempotency-Key. It never sends the ticket title, description, action, store,
identity, permissions or approval for a confirmation: the server executes the STORED
proposal. Error answers are fixed and never echo submitted values; a thread or proposal of
another actor or company, or in a store no longer granted, is 404.
"""

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, ConfigDict, StringConstraints

from app.context import CurrentActor, CurrentRequestContext
from app.employee_chat.errors import (
    ChatAgentDisabledError,
    ChatForbiddenError,
    ChatIdempotencyConflictError,
    ChatInvalidIdempotencyKeyError,
    ChatNotFoundError,
    ChatProposalAlreadyConfirmedError,
    ChatProposalCancelledError,
    ChatProposalNotFoundError,
    ChatTurnConflictError,
    ChatTurnInProgressError,
    EmployeeChatError,
)
from app.employee_chat.models import (
    MAX_MESSAGE_CHARS,
    ChatThread,
    ChatTurn,
    ProposalState,
    TicketProposal,
    TurnFailure,
    TurnStatus,
)
from app.employee_chat.service import EmployeeChatService, ThreadView
from app.routes.operations_tickets import IDEMPOTENCY_KEY_HEADER, http_status
from app.routes.validation import SafeValidationRoute
from app.services.operations_tickets import TicketCommandReason, TicketCommandStatus

EMPLOYEE_CHAT_SERVICE_STATE_KEY = "employee_chat_service"
CHAT_THREADS_PATH = "/api/v1/chat/threads"
CHAT_THREAD_PATH = "/api/v1/chat/thread"
CHAT_TURNS_PATH = "/api/v1/chat/turns"
CHAT_CONFIRM_PATH = "/api/v1/chat/ticket-proposals/confirm"
CHAT_CANCEL_PATH = "/api/v1/chat/ticket-proposals/cancel"
CHAT_PATHS = (
    CHAT_THREADS_PATH,
    CHAT_THREAD_PATH,
    CHAT_TURNS_PATH,
    CHAT_CONFIRM_PATH,
    CHAT_CANCEL_PATH,
)

router = APIRouter(tags=["chat"], route_class=SafeValidationRoute)
_FROZEN = ConfigDict(frozen=True, extra="forbid")

ChatMessage = Annotated[
    str,
    StringConstraints(
        strict=True, strip_whitespace=True, min_length=1, max_length=MAX_MESSAGE_CHARS
    ),
]


# ----- requests --------------------------------------------------------------------------------


class CreateThreadRequest(BaseModel):
    model_config = _FROZEN
    store_id: UUID


class TurnRequest(BaseModel):
    model_config = _FROZEN
    thread_id: UUID
    turn_id: UUID
    message: ChatMessage


class ProposalRequest(BaseModel):
    """Only the proposal id: never the title, description, action or store."""

    model_config = _FROZEN
    proposal_id: UUID


# ----- responses -------------------------------------------------------------------------------


class ThreadOut(BaseModel):
    model_config = _FROZEN
    thread_id: UUID
    store_id: str
    agent_id: str
    created_at: datetime
    updated_at: datetime

    @classmethod
    def of(cls, thread: ChatThread) -> "ThreadOut":
        return cls(
            thread_id=thread.thread_id,
            store_id=thread.store_id,
            agent_id=thread.agent_id,
            created_at=thread.created_at,
            updated_at=thread.updated_at,
        )


class TurnOut(BaseModel):
    model_config = _FROZEN
    turn_id: UUID
    sequence: int
    user_text: str
    assistant_text: str | None
    status: TurnStatus
    failure: TurnFailure | None
    created_at: datetime
    completed_at: datetime | None

    @classmethod
    def of(cls, turn: ChatTurn) -> "TurnOut":
        return cls(
            turn_id=turn.turn_id,
            sequence=turn.sequence,
            user_text=turn.user_text,
            assistant_text=turn.assistant_text,
            status=turn.status,
            failure=turn.failure,
            created_at=turn.created_at,
            completed_at=turn.completed_at,
        )


class ProposalOut(BaseModel):
    model_config = _FROZEN
    proposal_id: UUID
    turn_id: UUID
    action: str
    title: str
    description: str
    state: ProposalState
    command_id: UUID | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def of(cls, proposal: TicketProposal) -> "ProposalOut":
        return cls(
            proposal_id=proposal.proposal_id,
            turn_id=proposal.turn_id,
            action=proposal.action_name,
            title=proposal.title,
            description=proposal.description,
            state=proposal.state,
            command_id=proposal.command_id,
            created_at=proposal.created_at,
            updated_at=proposal.updated_at,
        )


class ThreadsResponse(BaseModel):
    model_config = _FROZEN
    request_id: UUID
    threads: tuple[ThreadOut, ...]


class ThreadResponse(BaseModel):
    model_config = _FROZEN
    request_id: UUID
    thread: ThreadOut


class ThreadDetailResponse(BaseModel):
    model_config = _FROZEN
    request_id: UUID
    thread: ThreadOut
    turns: tuple[TurnOut, ...]
    proposals: tuple[ProposalOut, ...]


class TurnsResponse(BaseModel):
    model_config = _FROZEN
    request_id: UUID
    thread_id: UUID
    turns: tuple[TurnOut, ...]
    proposals: tuple[ProposalOut, ...]


class TurnResponse(BaseModel):
    model_config = _FROZEN
    request_id: UUID
    replayed: bool
    turn: TurnOut
    proposal: ProposalOut | None


class TicketOut(BaseModel):
    """The governed WriteCommand's outcome: only ``verified`` means the ticket exists."""

    model_config = _FROZEN
    command_id: UUID
    status: TicketCommandStatus
    reason: TicketCommandReason | None
    ticket_id: UUID | None
    replayed: bool
    persistence_complete: bool


class ConfirmResponse(BaseModel):
    model_config = _FROZEN
    request_id: UUID
    proposal: ProposalOut
    ticket: TicketOut


class CancelResponse(BaseModel):
    model_config = _FROZEN
    request_id: UUID
    proposal: ProposalOut


# ----- helpers ---------------------------------------------------------------------------------

_STATUS: tuple[tuple[type[EmployeeChatError], int], ...] = (
    (ChatForbiddenError, status.HTTP_403_FORBIDDEN),
    (ChatNotFoundError, status.HTTP_404_NOT_FOUND),
    (ChatProposalNotFoundError, status.HTTP_404_NOT_FOUND),
    (ChatInvalidIdempotencyKeyError, status.HTTP_400_BAD_REQUEST),
    (ChatAgentDisabledError, status.HTTP_409_CONFLICT),
    (ChatTurnConflictError, status.HTTP_409_CONFLICT),
    (ChatTurnInProgressError, status.HTTP_409_CONFLICT),
    (ChatProposalCancelledError, status.HTTP_409_CONFLICT),
    (ChatProposalAlreadyConfirmedError, status.HTTP_409_CONFLICT),
    (ChatIdempotencyConflictError, status.HTTP_409_CONFLICT),
)
_UNAVAILABLE = "Employee chat unavailable"


def _unavailable() -> HTTPException:
    return HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=_UNAVAILABLE)


def _http(error: Exception) -> HTTPException:
    if isinstance(error, EmployeeChatError):
        for kind, code in _STATUS:
            if isinstance(error, kind):
                return HTTPException(status_code=code, detail=kind.message)
    return _unavailable()  # ChatUnavailableError or anything unexpected: never internals


def _service(request: Request) -> EmployeeChatService:
    service = getattr(request.app.state, EMPLOYEE_CHAT_SERVICE_STATE_KEY, None)
    if not isinstance(service, EmployeeChatService):
        raise _unavailable()
    return service


def _detail(request_id: UUID, view: ThreadView) -> ThreadDetailResponse:
    return ThreadDetailResponse(
        request_id=request_id,
        thread=ThreadOut.of(view.thread),
        turns=tuple(TurnOut.of(t) for t in view.turns),
        proposals=tuple(ProposalOut.of(p) for p in view.proposals),
    )


# ----- routes ----------------------------------------------------------------------------------


@router.get(CHAT_THREADS_PATH, response_model=ThreadsResponse)
async def list_chat_threads(
    store_id: Annotated[UUID, Query()],
    context: CurrentRequestContext,
    actor: CurrentActor,
    request: Request,
) -> ThreadsResponse:
    service = _service(request)
    try:
        threads = await service.list_threads(context, str(store_id))
        return ThreadsResponse(
            request_id=context.request_id, threads=tuple(ThreadOut.of(t) for t in threads)
        )
    except Exception as error:  # noqa: BLE001 - mapped to fixed answers
        raise _http(error) from None


@router.post(CHAT_THREADS_PATH, response_model=ThreadResponse, status_code=status.HTTP_201_CREATED)
async def create_chat_thread(
    body: CreateThreadRequest, context: CurrentRequestContext, actor: CurrentActor, request: Request
) -> ThreadResponse:
    service = _service(request)
    try:
        thread = await service.create_thread(context, str(body.store_id))
        return ThreadResponse(request_id=context.request_id, thread=ThreadOut.of(thread))
    except Exception as error:  # noqa: BLE001
        raise _http(error) from None


@router.get(CHAT_THREAD_PATH, response_model=ThreadDetailResponse)
async def get_chat_thread(
    thread_id: Annotated[UUID, Query()],
    context: CurrentRequestContext,
    actor: CurrentActor,
    request: Request,
) -> ThreadDetailResponse:
    service = _service(request)
    try:
        return _detail(context.request_id, await service.get_thread(context, thread_id))
    except Exception as error:  # noqa: BLE001
        raise _http(error) from None


@router.get(CHAT_TURNS_PATH, response_model=TurnsResponse)
async def list_chat_turns(
    thread_id: Annotated[UUID, Query()],
    context: CurrentRequestContext,
    actor: CurrentActor,
    request: Request,
) -> TurnsResponse:
    service = _service(request)
    try:
        view = await service.get_thread(context, thread_id)
        return TurnsResponse(
            request_id=context.request_id,
            thread_id=view.thread.thread_id,
            turns=tuple(TurnOut.of(t) for t in view.turns),
            proposals=tuple(ProposalOut.of(p) for p in view.proposals),
        )
    except Exception as error:  # noqa: BLE001
        raise _http(error) from None


@router.post(CHAT_TURNS_PATH, response_model=TurnResponse, status_code=status.HTTP_201_CREATED)
async def submit_chat_turn(
    body: TurnRequest,
    context: CurrentRequestContext,
    actor: CurrentActor,
    request: Request,
    response: Response,
) -> TurnResponse:
    service = _service(request)
    try:
        outcome = await service.submit_turn(context, body.thread_id, body.turn_id, body.message)
        payload = TurnResponse(
            request_id=context.request_id,
            replayed=outcome.replayed,
            turn=TurnOut.of(outcome.turn),
            proposal=ProposalOut.of(outcome.proposal) if outcome.proposal else None,
        )
    except Exception as error:  # noqa: BLE001
        raise _http(error) from None
    response.status_code = status.HTTP_200_OK if outcome.replayed else status.HTTP_201_CREATED
    return payload


@router.post(CHAT_CONFIRM_PATH, response_model=ConfirmResponse)
async def confirm_ticket_proposal(
    body: ProposalRequest,
    context: CurrentRequestContext,
    actor: CurrentActor,
    request: Request,
    response: Response,
) -> ConfirmResponse:
    keys = request.headers.getlist(IDEMPOTENCY_KEY_HEADER)
    if len(keys) != 1:  # missing, or ambiguous (sent more than once)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Idempotency-Key required"
        )
    service = _service(request)
    try:
        outcome = await service.confirm_ticket(context, body.proposal_id, keys[0])
        result = outcome.ticket
        payload = ConfirmResponse(
            request_id=context.request_id,
            proposal=ProposalOut.of(outcome.proposal),
            ticket=TicketOut(
                command_id=result.command_id,
                status=result.status,
                reason=result.reason,
                ticket_id=result.ticket_id,
                replayed=result.replayed,
                persistence_complete=result.persistence_complete,
            ),
        )
        code = http_status(result)
    except Exception as error:  # noqa: BLE001
        raise _http(error) from None
    response.status_code = code
    return payload


@router.post(CHAT_CANCEL_PATH, response_model=CancelResponse)
async def cancel_ticket_proposal(
    body: ProposalRequest, context: CurrentRequestContext, actor: CurrentActor, request: Request
) -> CancelResponse:
    service = _service(request)
    try:
        proposal = await service.cancel_ticket(context, body.proposal_id)
        return CancelResponse(request_id=context.request_id, proposal=ProposalOut.of(proposal))
    except Exception as error:  # noqa: BLE001
        raise _http(error) from None
