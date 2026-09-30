"""The Product-owned observability contract.

Observability answers "is the Product healthy and how is it behaving?". It is NOT
audit (governed business actions are recorded durably by ``app.persistence``), not
authorization, policy or verification, and never business truth.

    with observe(observability, ProductOperation.DAILY_REPORT, request_id) as observation:
        report = await service.get_daily_report(...)
        observation.finish(ObservationOutcome.COMPLETED)

Everything that can be observed is a fixed, low-cardinality vocabulary: the operation,
the outcome and a few bounded details (HTTP method/route/status, a canonical business
status and reason, two booleans). There is deliberately no free-form field: no
identifiers of companies, stores, actors, commands, tickets, orders or shipments, no
message, title, description, idempotency key, credential, provider value or exception
text can be expressed. ``request_id`` (the server-generated correlation id) is the
only identifier, and implementations must keep it out of metric attributes.

Observability is best-effort diagnostics: ``observe`` shields the observed operation
from any failure of the observability implementation (it never raises into business
code, never suppresses the operation's own exception and never retries anything).
"""

from dataclasses import dataclass
from enum import StrEnum
from types import TracebackType
from typing import Literal, Protocol, runtime_checkable
from uuid import UUID


class ProductOperation(StrEnum):
    """The stable names of the observed Product operations (never user-controlled)."""

    HTTP_REQUEST = "http.request"
    OPERATIONS_AGENT_RUN = "operations.agent_run"
    DAILY_REPORT = "operations.daily_report"
    TICKET_COMMAND = "operations.ticket_command"
    TICKET_COMMAND_QUERY = "operations.ticket_command_query"


class ObservationOutcome(StrEnum):
    """Stable outcome vocabulary. Never exception text, provider or model output."""

    COMPLETED = "completed"
    DENIED = "denied"
    INVALID = "invalid"
    CONFLICT = "conflict"
    NOT_FOUND = "not_found"
    UNAVAILABLE = "unavailable"
    ERROR = "error"


class ProductRoute(StrEnum):
    """The exact Product-owned HTTP paths that are observed (fixed, never raw paths)."""

    HEALTH = "/health"
    OPERATIONS_RUNS = "/api/v1/operations/runs"
    OPERATIONS_DAILY_REPORT = "/api/v1/operations/reports/daily"
    OPERATIONS_TICKETS = "/api/v1/operations/tickets"
    OPERATIONS_TICKET_COMMANDS = "/api/v1/operations/tickets/commands"


HttpMethod = Literal["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "OTHER"]
_HTTP_METHODS: frozenset[str] = frozenset(
    {"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"}
)


def bounded_http_method(method: object) -> HttpMethod:
    """A request method from the fixed set; anything else is ``OTHER``."""
    if isinstance(method, str) and method in _HTTP_METHODS:
        return method  # type: ignore[return-value]  # narrowed by the membership test
    return "OTHER"


@dataclass(frozen=True, slots=True)
class HttpDetails:
    method: HttpMethod
    route: ProductRoute
    status_code: int | None  # None when no response was started

    def __post_init__(self) -> None:
        if self.method not in _HTTP_METHODS and self.method != "OTHER":
            raise ValueError("unsupported http method")
        if not isinstance(self.route, ProductRoute):
            raise ValueError("route must be a fixed ProductRoute")
        if self.status_code is not None and (
            type(self.status_code) is not int or not 100 <= self.status_code <= 599
        ):
            raise ValueError("status_code must be an HTTP status code")


@dataclass(frozen=True, slots=True)
class BusinessDetails:
    """Canonical, bounded business result metadata (enum members only)."""

    status: StrEnum | None = None
    reason: StrEnum | None = None
    replayed: bool | None = None
    persistence_complete: bool | None = None

    def __post_init__(self) -> None:
        for value in (self.status, self.reason):
            if value is not None and not isinstance(value, StrEnum):
                raise ValueError("business status and reason must be canonical enum members")
        for flag in (self.replayed, self.persistence_complete):
            if flag is not None and type(flag) is not bool:
                raise ValueError("business flags must be booleans")


@dataclass(frozen=True, slots=True)
class ObservationDetails:
    http: HttpDetails | None = None
    business: BusinessDetails | None = None

    def attributes(self) -> dict[str, str | int | bool]:
        """The flat, bounded attribute set shared by logs, spans and metrics."""
        attrs: dict[str, str | int | bool] = {}
        if self.http is not None:
            attrs["http.method"] = self.http.method
            attrs["http.route"] = self.http.route.value
            if self.http.status_code is not None:
                attrs["http.status_code"] = self.http.status_code
        if self.business is not None:
            business = self.business
            if business.status is not None:
                attrs["business_status"] = business.status.value
            if business.reason is not None:
                attrs["business_reason"] = business.reason.value
            if business.replayed is not None:
                attrs["replayed"] = business.replayed
            if business.persistence_complete is not None:
                attrs["persistence_complete"] = business.persistence_complete
        return attrs


@runtime_checkable
class OperationObservation(Protocol):
    def finish(
        self, outcome: ObservationOutcome, details: ObservationDetails | None = None
    ) -> None:
        """Record the outcome once; later calls are ignored."""
        ...


class OperationScope(Protocol):
    """Context manager of one operation. Leaving it without ``finish`` records a
    generic ``error`` outcome."""

    def __enter__(self) -> OperationObservation: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> bool | None: ...


@runtime_checkable
class ProductObservability(Protocol):
    def operation(
        self, operation: ProductOperation, *, request_id: UUID | None = None
    ) -> OperationScope:
        """Start observing one Product operation."""
        ...


# ----- Failure isolation ------------------------------------------------------------------


class _NoObservation:
    def finish(
        self, outcome: ObservationOutcome, details: ObservationDetails | None = None
    ) -> None:
        return None


class _ShieldedObservation:
    """Forwards ``finish`` and swallows any failure of the implementation."""

    __slots__ = ("_inner",)

    def __init__(self, inner: OperationObservation) -> None:
        self._inner = inner

    def finish(
        self, outcome: ObservationOutcome, details: ObservationDetails | None = None
    ) -> None:
        try:
            self._inner.finish(outcome, details)
        except Exception:  # noqa: BLE001, S110 - observability never affects the Product
            pass


class observe:  # noqa: N801 - used like a function: ``with observe(...)``
    """Observe one operation through ``observability``, shielding the operation.

    Any exception raised by the observability implementation (starting, finishing or
    closing the observation) is swallowed. The operation's own exception always
    propagates unchanged: this context manager never suppresses it.
    """

    __slots__ = ("_observability", "_operation", "_request_id", "_scope")

    def __init__(
        self,
        observability: ProductObservability | None,
        operation: ProductOperation,
        request_id: object = None,
    ) -> None:
        self._observability = observability
        self._operation = operation
        self._request_id = request_id if isinstance(request_id, UUID) else None
        self._scope: OperationScope | None = None

    def __enter__(self) -> OperationObservation:
        if self._observability is None:
            return _NoObservation()
        try:
            scope = self._observability.operation(self._operation, request_id=self._request_id)
            inner = scope.__enter__()
        except Exception:  # noqa: BLE001 - observability never affects the Product
            return _NoObservation()
        self._scope = scope
        return _ShieldedObservation(inner)

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> Literal[False]:
        scope, self._scope = self._scope, None
        if scope is not None:
            try:
                scope.__exit__(exc_type, exc, tb)
            except Exception:  # noqa: BLE001, S110 - observability never affects the Product
                pass
        return False  # never suppress the observed operation's exception

    def __repr__(self) -> str:
        return f"observe({self._operation.value})"
