"""``POST /api/v1/operations/tickets``: the deterministic, durable ticket write, and
``GET /api/v1/operations/tickets/commands?command_id=``: its read-only status.

    HTTP -> RequestContextMiddleware -> ActorResolver -> trusted ActorContext (401 if none)
         -> strict body {store_id, title, description}                 (422 otherwise)
         -> exact store grant check against actor.store_ids             (403 otherwise)
         -> trusted ActionScope(company_id=actor.company_id, store_id=<granted store>)
         -> exactly one Idempotency-Key header                          (400 otherwise)
         -> OperationsTicketCommandService.create_ticket                 (none: 503)
         -> {request_id, command_id, status, reason, ticket_id, replayed,
             persistence_complete}

Not an agent endpoint: no model is involved and the action is fixed server-side by
the service. Product authentication is the ``ActorResolver``; the AgentOS
``OS_SECURITY_KEY`` is not. The client never supplies identity, company, action,
permissions or approval. The key is opaque: it is passed through unchanged (the
command layer validates it), never logged, stored in plaintext or returned.

The HTTP status reports how the durable command was processed; ``status`` in the
body is the business outcome, and only ``verified`` means the ticket was created.

The status GET is a pure read of the DURABLE command state:

    HTTP -> RequestContextMiddleware -> ActorResolver -> trusted ActorContext (401)
         -> command_id query parameter (UUID, 422 otherwise)
         -> OperationsTicketCommandQueryService.get_command (none: 503)
         -> 404 "Ticket command not found" for unknown, another principal's, another
            action or a store the actor is not currently granted (indistinguishable)
         -> {request_id, command_id, status, reason, ticket_id, created_at, updated_at}

It never submits, retries or executes anything, needs no Idempotency-Key (one sent is
ignored), and takes no store, action or identity from the client.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request, Response, status
from pydantic import AwareDatetime, BaseModel, ConfigDict, StringConstraints

from app.context import CurrentActor, CurrentRequestContext
from app.governance import ActionScope
from app.services.operations_tickets import (
    IdempotencyConflictError,
    InvalidIdempotencyKeyError,
    OperationsTicketCommandQueryService,
    OperationsTicketCommandService,
    ProductTicketCommandResult,
    ProductTicketCommandStatusResult,
    TicketCommandNotFoundError,
    TicketCommandReason,
    TicketCommandStatus,
)

OPERATIONS_TICKET_SERVICE_STATE_KEY = "operations_ticket_service"
OPERATIONS_TICKETS_PATH = "/api/v1/operations/tickets"
OPERATIONS_TICKET_QUERY_SERVICE_STATE_KEY = "operations_ticket_query_service"
# A FIXED path (the id is a query parameter), so the AgentOS auth exemption stays an
# exact path rather than a prefix or pattern.
OPERATIONS_TICKET_COMMANDS_PATH = "/api/v1/operations/tickets/commands"
IDEMPOTENCY_KEY_HEADER = "Idempotency-Key"

router = APIRouter(tags=["operations"])


class OperationsTicketRequest(BaseModel):
    """The only client-controlled inputs: a target store selector and the ticket text."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    store_id: UUID
    title: Annotated[
        str, StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=160)
    ]
    description: Annotated[
        str, StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=4000)
    ]


class OperationsTicketResponse(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    request_id: UUID
    command_id: UUID
    status: TicketCommandStatus
    reason: TicketCommandReason | None
    ticket_id: UUID | None
    replayed: bool
    persistence_complete: bool


def http_status(result: ProductTicketCommandResult) -> int:
    S = TicketCommandStatus
    if not result.persistence_complete:
        return status.HTTP_202_ACCEPTED
    if result.status is S.VERIFIED:
        return status.HTTP_200_OK if result.replayed else status.HTTP_201_CREATED
    if result.status in (S.IN_PROGRESS, S.AWAITING_APPROVAL, S.REQUIRES_HUMAN):
        return status.HTTP_202_ACCEPTED
    return status.HTTP_200_OK  # DENIED, FAILED: processed; the outcome is in ``status``


def _service(request: Request) -> OperationsTicketCommandService | None:
    service = getattr(request.app.state, OPERATIONS_TICKET_SERVICE_STATE_KEY, None)
    return service if isinstance(service, OperationsTicketCommandService) else None


def _unavailable() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="Operations ticket service unavailable",
    )


@router.post(
    OPERATIONS_TICKETS_PATH,
    response_model=OperationsTicketResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_operations_ticket(
    body: OperationsTicketRequest,
    context: CurrentRequestContext,
    actor: CurrentActor,
    request: Request,
    response: Response,
) -> OperationsTicketResponse:
    # The store is a client-selected target; it becomes trusted scope only if the
    # authenticated actor was explicitly granted exactly this store.
    store_id = str(body.store_id)
    if store_id not in actor.store_ids:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")
    scope = ActionScope(company_id=actor.company_id, store_id=store_id)

    keys = request.headers.getlist(IDEMPOTENCY_KEY_HEADER)
    if len(keys) != 1:  # missing, or ambiguous (sent more than once)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Idempotency-Key required"
        )

    service = _service(request)
    if service is None:
        raise _unavailable()
    # Everything that depends on the service's answer stays inside the fail-closed
    # boundary: a runtime-checkable Protocol does not guarantee the result type.
    try:
        result = await service.create_ticket(context, scope, body.title, body.description, keys[0])
        if not isinstance(result, ProductTicketCommandResult):
            raise TypeError("invalid ticket service result")
        payload = OperationsTicketResponse(
            request_id=context.request_id,
            command_id=result.command_id,
            status=result.status,
            reason=result.reason,
            ticket_id=result.ticket_id,
            replayed=result.replayed,
            persistence_complete=result.persistence_complete,
        )
        code = http_status(result)
    except InvalidIdempotencyKeyError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid Idempotency-Key"
        ) from None
    except IdempotencyConflictError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Idempotency conflict"
        ) from None
    except Exception:  # noqa: BLE001 - never leak internals; the request id correlates logs
        raise _unavailable() from None
    response.status_code = code
    return payload


# ----- GET: durable ticket command status (read-only) ---------------------------------------


class OperationsTicketCommandStatusResponse(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    request_id: UUID  # this GET's request id, never the original POST's
    command_id: UUID
    status: TicketCommandStatus
    reason: TicketCommandReason | None
    ticket_id: UUID | None
    created_at: AwareDatetime
    updated_at: AwareDatetime


def _query_service(request: Request) -> OperationsTicketCommandQueryService | None:
    service = getattr(request.app.state, OPERATIONS_TICKET_QUERY_SERVICE_STATE_KEY, None)
    return service if isinstance(service, OperationsTicketCommandQueryService) else None


def _query_unavailable() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="Operations ticket query service unavailable",
    )


@router.get(OPERATIONS_TICKET_COMMANDS_PATH, response_model=OperationsTicketCommandStatusResponse)
async def get_operations_ticket_command(
    command_id: Annotated[UUID, Query()],
    context: CurrentRequestContext,
    actor: CurrentActor,  # authentication only; the service scopes by the trusted actor
    request: Request,
) -> OperationsTicketCommandStatusResponse:
    service = _query_service(request)
    if service is None:
        raise _query_unavailable()
    try:
        result = await service.get_command(context, command_id)
        if not isinstance(result, ProductTicketCommandStatusResult):
            raise TypeError("invalid ticket query result")
        response = OperationsTicketCommandStatusResponse(
            request_id=context.request_id,
            command_id=result.command_id,
            status=result.status,
            reason=result.reason,
            ticket_id=result.ticket_id,
            created_at=result.created_at,
            updated_at=result.updated_at,
        )
    except TicketCommandNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Ticket command not found"
        ) from None
    except Exception:  # noqa: BLE001 - never leak internals; the request id correlates logs
        raise _query_unavailable() from None
    return response
