"""Product HTTP observability: plain ASGI middleware, streaming-safe.

Observes ONLY the exact Product-owned paths (``ProductRoute``), never AgentOS or any
other path, and records the bounded method, the fixed route and the response status.
It never reads the body, query string or headers, and never buffers: it only watches
the ``http.response.start`` message on its way out.

It runs INSIDE ``RequestContextMiddleware`` (which must stay outermost), so the
server-generated request id of the trusted ``RequestContext`` is available.

    response status    -> outcome
    2xx / 3xx          -> completed
    400, 422           -> invalid
    401, 403           -> denied
    404                -> not_found
    409                -> conflict
    503 / other 5xx    -> unavailable / error
    exception raised before completion -> error (the exception is re-raised unchanged)
"""

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.observability.contracts import (
    HttpDetails,
    ObservationDetails,
    ObservationOutcome,
    ProductObservability,
    ProductOperation,
    ProductRoute,
    bounded_http_method,
    observe,
)

_ROUTES: dict[str, ProductRoute] = {route.value: route for route in ProductRoute}
# The key RequestContextMiddleware stores the trusted context under (request.state).
_REQUEST_CONTEXT_STATE_KEY = "request_context"

Outcome = ObservationOutcome


def outcome_for_status(status_code: int) -> ObservationOutcome:
    if status_code < 400:
        return Outcome.COMPLETED
    if status_code in (400, 422):
        return Outcome.INVALID
    if status_code in (401, 403):
        return Outcome.DENIED
    if status_code == 404:
        return Outcome.NOT_FOUND
    if status_code == 409:
        return Outcome.CONFLICT
    if status_code == 503:
        return Outcome.UNAVAILABLE
    return Outcome.ERROR if status_code >= 500 else Outcome.INVALID


class ProductObservabilityMiddleware:
    def __init__(self, app: ASGIApp, observability: ProductObservability) -> None:
        self.app = app
        self.observability = observability

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        route = _ROUTES.get(scope.get("path", "")) if scope["type"] == "http" else None
        if route is None:
            await self.app(scope, receive, send)
            return

        context = scope.get("state", {}).get(_REQUEST_CONTEXT_STATE_KEY)
        request_id = getattr(context, "request_id", None)
        method = bounded_http_method(scope.get("method"))
        status_code: int | None = None
        completed = False

        async def observed_send(message: Message) -> None:
            nonlocal status_code, completed
            if message["type"] == "http.response.start":
                status = message.get("status")
                status_code = status if type(status) is int else None
            elif message["type"] == "http.response.body" and not message.get("more_body"):
                completed = True
            await send(message)

        with observe(self.observability, ProductOperation.HTTP_REQUEST, request_id) as obs:
            try:
                await self.app(scope, receive, observed_send)
            except BaseException:
                obs.finish(Outcome.ERROR, _details(method, route, status_code))
                raise
            if status_code is None or not completed:
                obs.finish(Outcome.ERROR, _details(method, route, status_code))
            else:
                obs.finish(outcome_for_status(status_code), _details(method, route, status_code))


def _details(method, route: ProductRoute, status_code: int | None) -> ObservationDetails | None:
    try:
        return ObservationDetails(http=HttpDetails(method=method, route=route,
                                                    status_code=status_code))  # fmt: skip
    except Exception:  # noqa: BLE001 - invalid details are dropped, never guessed
        return None
