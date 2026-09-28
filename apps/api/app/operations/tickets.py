"""``operations.ticket.create``: the first governed business write.

``CreateOperationalTicketHandler`` runs only inside ``ExecutionCoordinator`` (after
governance allowed the action and the input validated). It depends on the
``TicketingIntegration`` contract, never on a concrete provider or the mock.

- validate: raw parameters -> frozen ``CreateOperationalTicketInput`` (title and
  description only; company, store, actor, run and correlation come from context).
- execute: trusted ``ActionExecutionContext`` supplies company/store (canonical UUID
  strings) and the run id, used as the correlation id. Returns only the canonical
  ticket id as the reference.
- verify: re-reads the ticket independently by correlation and compares it with the
  trusted scope and validated input; the create call's answer is never the proof.
"""

from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, StringConstraints

from app.commerce.domain import Ticket, TicketStatus
from app.execution import (
    ActionExecutionContext,
    ExecutionFailedWithoutEffect,
    ExecutionOutcomeUncertain,
    ExecutionResult,
    RawParameters,
    VerificationResult,
)
from app.integrations.commerce import IntegrationWriteError, TicketingIntegration
from app.operations.actions import CREATE_TICKET_ACTION

TicketTitle = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=160)]
TicketDescription = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)
]


class CreateOperationalTicketInput(BaseModel):
    """User-controlled business content only. Scope and identity are never input."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    title: TicketTitle
    description: TicketDescription


class _UntrustedScopeError(Exception):
    """The trusted context does not carry canonical company/store UUIDs."""


def _trusted_scope(context: ActionExecutionContext) -> tuple[UUID, UUID]:
    """Canonical (company, store) UUIDs from the trusted context. Never invented,
    never taken from parameters."""
    if context.store_id is None:
        raise _UntrustedScopeError
    try:
        return UUID(context.company_id), UUID(context.store_id)
    except ValueError as exc:
        raise _UntrustedScopeError from exc


class CreateOperationalTicketHandler:
    def __init__(self, tickets: TicketingIntegration) -> None:
        self._tickets = tickets

    @property
    def action_name(self) -> str:
        return CREATE_TICKET_ACTION.name

    def validate(self, parameters: RawParameters) -> CreateOperationalTicketInput:
        return CreateOperationalTicketInput.model_validate(dict(parameters))

    async def execute(
        self, context: ActionExecutionContext, validated_input: BaseModel
    ) -> ExecutionResult:
        data = self._input(validated_input)
        try:
            company_id, store_id = _trusted_scope(context)
        except _UntrustedScopeError:
            # Malformed trusted configuration: stop before any external call.
            raise ExecutionFailedWithoutEffect("trusted scope is not canonical") from None

        try:
            ticket = await self._tickets.create_ticket(
                company_id=company_id,
                store_id=store_id,
                title=data.title,
                description=data.description,
                correlation_id=context.run_id,
            )
        except IntegrationWriteError as exc:
            if exc.effect_may_have_occurred:
                raise ExecutionOutcomeUncertain("ticket write may have happened") from None
            raise ExecutionFailedWithoutEffect("ticket write did not happen") from None
        except Exception:  # noqa: BLE001 - unknown failure inside the external write
            raise ExecutionOutcomeUncertain("ticket write outcome unknown") from None

        if not isinstance(ticket, Ticket):
            raise ExecutionOutcomeUncertain("ticket write returned no canonical ticket")
        return ExecutionResult(reference_id=str(ticket.id))

    async def verify(
        self,
        context: ActionExecutionContext,
        validated_input: BaseModel,
        execution_result: ExecutionResult | None,
    ) -> VerificationResult:
        data = self._input(validated_input)
        try:
            company_id, store_id = _trusted_scope(context)
        except _UntrustedScopeError:
            return VerificationResult(verified=False, reason_code="ticket_scope_invalid")

        ticket = await self._tickets.find_ticket_by_correlation(context.run_id)
        if ticket is None:
            return VerificationResult(verified=False, reason_code="ticket_missing")
        if (
            ticket.company_id != company_id
            or ticket.store_id != store_id
            or ticket.title != data.title
            or ticket.description != data.description
            or ticket.status is not TicketStatus.OPEN
        ):
            return VerificationResult(verified=False, reason_code="ticket_mismatch")
        if execution_result is not None and str(ticket.id) != execution_result.reference_id:
            return VerificationResult(verified=False, reason_code="ticket_reference_mismatch")
        return VerificationResult(verified=True, reason_code="ticket_present")

    @staticmethod
    def _input(validated_input: BaseModel) -> CreateOperationalTicketInput:
        if not isinstance(validated_input, CreateOperationalTicketInput):
            raise TypeError("expected CreateOperationalTicketInput")
        return validated_input
