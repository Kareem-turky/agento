"""``OperationsTicketCommandQueryService`` on the principal-scoped command reader.

    trusted request (actor required) + command_id
    -> WriteCommandReader.get_for_actor(command_id, actor.company_id, actor.actor_id)
       (scoped in the query: another principal's command is never loaded)
    -> not found, another action, no store, or a store the actor is not CURRENTLY
       granted (exact membership)                       -> TicketCommandNotFoundError
    -> the durable state as ProductTicketCommandStatusResult

Read-only: nothing is claimed, completed, governed, executed, verified or audited.
Current ``tickets.create`` is not required: reading the status of one's own command in
a currently granted store performs no new write. ``ticket_id`` only for VERIFIED.
"""

from uuid import UUID

from pydantic import ValidationError

from app.commands import CommandStatus, WriteCommandReader, WriteCommandRecord
from app.context.models import RequestContext
from app.operations.actions import CREATE_TICKET_ACTION
from app.services.operations_tickets import (
    ProductTicketCommandStatusResult,
    TicketCommandNotFoundError,
    TicketCommandQueryUnavailableError,
    TicketCommandReason,
    TicketCommandStatus,
)


def _status_result(record: WriteCommandRecord) -> ProductTicketCommandStatusResult:
    ticket_id = None
    if record.status is CommandStatus.VERIFIED:
        if record.execution_reference_id is None:
            raise ValueError("verified without a ticket reference")
        ticket_id = UUID(record.execution_reference_id)  # canonical ticket UUID only
    return ProductTicketCommandStatusResult(
        command_id=record.command_id,
        status=TicketCommandStatus(record.status.value),
        reason=TicketCommandReason(record.reason.value) if record.reason else None,
        ticket_id=ticket_id,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


class WriteCommandTicketQueryService:
    """Implements ``OperationsTicketCommandQueryService`` with an injected reader."""

    def __init__(self, reader: WriteCommandReader) -> None:
        self._reader = reader

    async def get_command(
        self, request: RequestContext, command_id: UUID
    ) -> ProductTicketCommandStatusResult:
        actor = request.actor
        if actor is None:
            raise TicketCommandNotFoundError()  # no principal, nothing is visible
        try:
            record = await self._reader.get_for_actor(command_id, actor.company_id, actor.actor_id)
        except Exception:  # noqa: BLE001 - never leak storage details
            raise TicketCommandQueryUnavailableError() from None
        if record is None:
            raise TicketCommandNotFoundError()
        if not isinstance(record, WriteCommandRecord) or (
            record.command_id != command_id
            or record.company_id != actor.company_id
            or record.actor_id != actor.actor_id
        ):
            raise TicketCommandQueryUnavailableError()  # the reader broke its contract
        if (
            record.action_name != CREATE_TICKET_ACTION.name
            or record.store_id is None
            or record.store_id not in actor.store_ids
        ):
            raise TicketCommandNotFoundError()  # indistinguishable from unknown
        try:
            return _status_result(record)
        except (TypeError, ValueError, ValidationError):
            raise TicketCommandQueryUnavailableError() from None
