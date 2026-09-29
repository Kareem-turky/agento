"""``OperationsTicketCommandService`` implemented on the durable write-command layer.

    trusted request + scope, untrusted title/description, caller's Idempotency-Key
    -> WriteCommandCoordinator.submit(action = operations.ticket.create, FIXED here,
       parameters = {title, description} only, key passed through unchanged)
    -> WriteCommandResult -> safe ProductTicketCommandResult

The action is never chosen by the caller. Command-layer errors become safe product
errors (no exception text crosses the contract). ``ticket_id`` is exposed only for
VERIFIED; any internal execution reference of another outcome is suppressed, and a
VERIFIED command without a canonical ticket UUID fails closed.
"""

from uuid import UUID

from pydantic import ValidationError

from app.commands import (
    CommandStatus,
    IdempotencyConflictError,
    InvalidIdempotencyKeyError,
    WriteCommandCoordinator,
    WriteCommandResult,
)
from app.context.models import RequestContext
from app.governance import ActionIntent, ActionScope
from app.operations.actions import CREATE_TICKET_ACTION
from app.services import operations_tickets as product
from app.services.operations_tickets import (
    ProductTicketCommandResult,
    TicketCommandReason,
    TicketCommandStatus,
)


def _ticket_id(result: WriteCommandResult) -> UUID | None:
    if result.status is not CommandStatus.VERIFIED:
        return None  # never imply creation without verification
    if result.execution_reference_id is None:
        raise ValueError("verified without a ticket reference")
    return UUID(result.execution_reference_id)  # canonical ticket UUID, else ValueError


def _product_result(result: object) -> ProductTicketCommandResult:
    if not isinstance(result, WriteCommandResult) or (
        result.action_name != CREATE_TICKET_ACTION.name
    ):
        raise TypeError("invalid command result")
    return ProductTicketCommandResult(
        command_id=result.command_id,
        status=TicketCommandStatus(result.status.value),
        reason=TicketCommandReason(result.reason.value) if result.reason else None,
        ticket_id=_ticket_id(result),
        replayed=result.replayed,
        persistence_complete=result.persistence_complete,
    )


class WriteCommandTicketService:
    """Implements ``OperationsTicketCommandService`` with an injected coordinator."""

    def __init__(self, commands: WriteCommandCoordinator) -> None:
        self._commands = commands

    async def create_ticket(
        self,
        request: RequestContext,
        scope: ActionScope,
        title: str,
        description: str,
        idempotency_key: str,
    ) -> ProductTicketCommandResult:
        try:
            result = await self._commands.submit(
                request,
                scope,
                ActionIntent(name=CREATE_TICKET_ACTION.name),
                {"title": title, "description": description},
                idempotency_key,
            )
        except InvalidIdempotencyKeyError:
            raise product.InvalidIdempotencyKeyError() from None
        except IdempotencyConflictError:
            raise product.IdempotencyConflictError() from None
        except Exception:  # noqa: BLE001 - store failure or anything unexpected
            raise product.TicketCommandUnavailableError() from None
        try:
            return _product_result(result)
        except (TypeError, ValueError, ValidationError):
            raise product.TicketCommandUnavailableError() from None
