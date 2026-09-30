"""Observed Product services: transparent decorators of the service contracts.

Each wrapper implements the SAME Product service Protocol and delegates to the
original service with the same arguments, returning the same result object and
re-raising the same exception object. It only adds one observation (stable
operation name, outcome and bounded canonical metadata) through the shielded
``observe`` helper, so an observability failure can never change, fail or retry the
operation. Routes keep depending on the service contracts; business services never
know they are observed.

Never observed: messages, titles, descriptions, idempotency keys, scopes, actors,
report contents, business dates, command or ticket identifiers, model output.
"""

from datetime import date
from uuid import UUID

from app.context.models import RequestContext
from app.governance import ActionScope
from app.observability.contracts import (
    BusinessDetails,
    ObservationDetails,
    ObservationOutcome,
    ProductObservability,
    ProductOperation,
    observe,
)
from app.services.operations import OperationsRunService, ProductOperationsRunResult
from app.services.operations_reports import (
    DailyOperationsForbiddenError,
    DailyOperationsReport,
    DailyOperationsReportService,
    DailyOperationsUnavailableError,
)
from app.services.operations_tickets import (
    IdempotencyConflictError,
    InvalidIdempotencyKeyError,
    OperationsTicketCommandQueryService,
    OperationsTicketCommandService,
    ProductTicketCommandResult,
    ProductTicketCommandStatusResult,
    TicketCommandNotFoundError,
    TicketCommandQueryUnavailableError,
    TicketCommandUnavailableError,
)

Outcome = ObservationOutcome


def _request_id(request: object) -> UUID | None:
    value = getattr(request, "request_id", None)
    return value if isinstance(value, UUID) else None


class ObservedOperationsRunService:
    """Run count, latency and completion/failure only: never the message, the model
    output, tool payloads or Agno internals (no token or cost telemetry)."""

    def __init__(self, delegate: OperationsRunService, observability: ProductObservability):
        self.delegate = delegate
        self._observability = observability

    async def run_product(
        self, request: RequestContext, scope: ActionScope, message: str
    ) -> ProductOperationsRunResult:
        with observe(
            self._observability, ProductOperation.OPERATIONS_AGENT_RUN, _request_id(request)
        ) as observation:
            result = await self.delegate.run_product(request, scope, message)
            observation.finish(Outcome.COMPLETED)
            return result


class ObservedDailyOperationsReportService:
    """Report completion/denial/unavailability only: never the report, store or date."""

    def __init__(self, delegate: DailyOperationsReportService, observability: ProductObservability):
        self.delegate = delegate
        self._observability = observability

    async def get_daily_report(
        self, request: RequestContext, scope: ActionScope, business_date: date | None
    ) -> DailyOperationsReport:
        with observe(
            self._observability, ProductOperation.DAILY_REPORT, _request_id(request)
        ) as observation:
            try:
                report = await self.delegate.get_daily_report(request, scope, business_date)
            except DailyOperationsForbiddenError:
                observation.finish(Outcome.DENIED)
                raise
            except DailyOperationsUnavailableError:
                observation.finish(Outcome.UNAVAILABLE)
                raise
            observation.finish(Outcome.COMPLETED)
            return report


class ObservedOperationsTicketCommandService:
    """Canonical command status/reason, ``replayed`` and ``persistence_complete`` only:
    never the title, description, idempotency key, command or ticket id, store or actor."""

    def __init__(
        self, delegate: OperationsTicketCommandService, observability: ProductObservability
    ):
        self.delegate = delegate
        self._observability = observability

    async def create_ticket(
        self,
        request: RequestContext,
        scope: ActionScope,
        title: str,
        description: str,
        idempotency_key: str,
    ) -> ProductTicketCommandResult:
        with observe(
            self._observability, ProductOperation.TICKET_COMMAND, _request_id(request)
        ) as observation:
            try:
                result = await self.delegate.create_ticket(
                    request, scope, title, description, idempotency_key
                )
            except InvalidIdempotencyKeyError:
                observation.finish(Outcome.INVALID)
                raise
            except IdempotencyConflictError:
                observation.finish(Outcome.CONFLICT)
                raise
            except TicketCommandUnavailableError:
                observation.finish(Outcome.UNAVAILABLE)
                raise
            if isinstance(result, ProductTicketCommandResult):
                details = ObservationDetails(
                    business=BusinessDetails(
                        status=result.status,
                        reason=result.reason,
                        replayed=result.replayed,
                        persistence_complete=result.persistence_complete,
                    )
                )
                observation.finish(Outcome.COMPLETED, details)
            # Anything else is left to the route's fail-closed check (generic error).
            return result


class ObservedOperationsTicketCommandQueryService:
    """Durable canonical status/reason only: never the command or ticket id."""

    def __init__(
        self, delegate: OperationsTicketCommandQueryService, observability: ProductObservability
    ):
        self.delegate = delegate
        self._observability = observability

    async def get_command(
        self, request: RequestContext, command_id: UUID
    ) -> ProductTicketCommandStatusResult:
        with observe(
            self._observability, ProductOperation.TICKET_COMMAND_QUERY, _request_id(request)
        ) as observation:
            try:
                result = await self.delegate.get_command(request, command_id)
            except TicketCommandNotFoundError:
                observation.finish(Outcome.NOT_FOUND)
                raise
            except TicketCommandQueryUnavailableError:
                observation.finish(Outcome.UNAVAILABLE)
                raise
            if isinstance(result, ProductTicketCommandStatusResult):
                details = ObservationDetails(
                    business=BusinessDetails(status=result.status, reason=result.reason)
                )
                observation.finish(Outcome.COMPLETED, details)
            return result


# ----- Wrapping composed services (``create_app``) -----------------------------------------
# ``None`` stays ``None`` (the route answers 503 as before) and an object that does not
# implement the slot's contract is left untouched (the route's own check decides).


def observed_operations_service(
    service: OperationsRunService | None, observability: ProductObservability
) -> OperationsRunService | None:
    if isinstance(service, OperationsRunService):
        return ObservedOperationsRunService(service, observability)
    return service


def observed_daily_operations_service(
    service: DailyOperationsReportService | None, observability: ProductObservability
) -> DailyOperationsReportService | None:
    if isinstance(service, DailyOperationsReportService):
        return ObservedDailyOperationsReportService(service, observability)
    return service


def observed_ticket_service(
    service: OperationsTicketCommandService | None, observability: ProductObservability
) -> OperationsTicketCommandService | None:
    if isinstance(service, OperationsTicketCommandService):
        return ObservedOperationsTicketCommandService(service, observability)
    return service


def observed_ticket_query_service(
    service: OperationsTicketCommandQueryService | None, observability: ProductObservability
) -> OperationsTicketCommandQueryService | None:
    if isinstance(service, OperationsTicketCommandQueryService):
        return ObservedOperationsTicketCommandQueryService(service, observability)
    return service
