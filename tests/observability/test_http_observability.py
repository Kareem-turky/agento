"""Product HTTP observability end to end through ``create_app`` (no database, no model).

Real FastAPI app, RequestContextMiddleware, ProductObservabilityMiddleware and the
AgentOS auth layer; fake Product services and a test ActorResolver.
"""

import json
from datetime import UTC, date, datetime
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from app.context import ActorContext, RequestContextMiddleware
from app.main import create_app
from app.observability import (
    ObservationOutcome,
    ProductObservabilityMiddleware,
    ProductOperation,
    ProductRoute,
)
from app.routes.operations_tickets import OPERATIONS_TICKET_COMMANDS_PATH, OPERATIONS_TICKETS_PATH
from app.services.operations import ProductOperationsRunResult
from app.services.operations_reports import DailyOperationsForbiddenError
from app.services.operations_tickets import (
    IdempotencyConflictError,
    InvalidIdempotencyKeyError,
    ProductTicketCommandResult,
    ProductTicketCommandStatusResult,
    TicketCommandNotFoundError,
    TicketCommandReason,
    TicketCommandStatus,
)
from tests.agents.helpers import COMPANY, OTHER_STORE, STORE, actor
from tests.support.actor_resolver import StaticActorResolver
from tests.support.observability import (
    FAILURE_POINTS,
    FailingObservability,
    RecordingObservability,
    attrs,
    ctx,
    harness,
    parent,
)
from tests.support.product_auth import deployment_settings, principal

Out, P = ObservationOutcome, ProductOperation
S, R = TicketCommandStatus, TicketCommandReason
RUNS, REPORT = "/api/v1/operations/runs", "/api/v1/operations/reports/daily"
TICKETS, COMMANDS = OPERATIONS_TICKETS_PATH, OPERATIONS_TICKET_COMMANDS_PATH
WRITER = actor(permissions=frozenset({"tickets.create", "orders.read", "shipments.read"}))
COMMAND, TICKET = UUID(int=0xC0FFEE), UUID(int=0x71C7E7)
NOW = datetime(2026, 3, 3, 12, tzinfo=UTC)

MESSAGE = "OBS-SECRET-MESSAGE-please-analyze"
TITLE, DESCRIPTION = "OBS-SECRET-TITLE-x", "OBS-SECRET-DESCRIPTION-y"
IDEMPOTENCY_KEY = "OBS-IDEMPOTENCY-0001-abcdef"
AUTHORIZATION = "Bearer OBS-AUTH-not-a-real-token"
PRODUCT_KEY = "OBS-AUTH-product-key-" + "b" * 32  # obviously test-only


# ----- Fake Product services ---------------------------------------------------------------


def ticket_result(**overrides) -> ProductTicketCommandResult:
    data = {"command_id": COMMAND, "status": S.VERIFIED, "reason": R.VERIFIED,
            "ticket_id": TICKET, "replayed": False, "persistence_complete": True}  # fmt: skip
    return ProductTicketCommandResult(**(data | overrides))


class Runs:
    def __init__(self, raises: BaseException | None = None) -> None:
        self.raises, self.calls = raises, 0

    async def run_product(self, request, scope, message):
        self.calls += 1
        if self.raises is not None:
            raise self.raises
        return ProductOperationsRunResult(message="assistant reply")


class Reports:
    def __init__(self, raises: BaseException | None = None) -> None:
        self.raises, self.calls = raises, 0

    async def get_daily_report(self, request, scope, business_date):
        self.calls += 1
        raise self.raises or DailyOperationsForbiddenError()


class Tickets:
    def __init__(self, result=None, raises: BaseException | None = None) -> None:
        self.result, self.raises, self.calls = result or ticket_result(), raises, 0

    async def create_ticket(self, request, scope, title, description, idempotency_key):
        self.calls += 1
        if self.raises is not None:
            raise self.raises
        return self.result


class Queries:
    def __init__(self, raises: BaseException | None = None) -> None:
        self.raises, self.calls = raises, 0

    async def get_command(self, request, command_id):
        self.calls += 1
        if self.raises is not None:
            raise self.raises
        return ProductTicketCommandStatusResult(
            command_id=command_id, status=S.VERIFIED, reason=R.VERIFIED, ticket_id=TICKET,
            created_at=NOW, updated_at=NOW,
        )  # fmt: skip


def build(settings, runtime_settings, observability, who: ActorContext | None = WRITER,
          **services):  # fmt: skip
    return create_app(settings, runtime_settings, observability=observability,
                      actor_resolver=StaticActorResolver(who) if who else None,
                      **services)  # fmt: skip


def all_services(**overrides):
    services = {"operations_service": Runs(), "daily_operations_service": Reports(),
                "operations_ticket_service": Tickets(),
                "operations_ticket_query_service": Queries()}  # fmt: skip
    return services | overrides


def exercise(client: TestClient):
    """One request per observed route; returns the responses in order."""
    return [
        client.get("/health"),
        client.post(RUNS, json={"message": MESSAGE, "store_id": STORE}),
        client.get(REPORT, params={"store_id": STORE}),
        client.post(
            TICKETS,
            json={"store_id": STORE, "title": TITLE, "description": DESCRIPTION},
            headers={"Idempotency-Key": IDEMPOTENCY_KEY},
        ),  # fmt: skip
        client.get(COMMANDS, params={"command_id": str(COMMAND)}),
    ]


# ----- Middleware ordering and coverage ----------------------------------------------------


def test_request_context_stays_outermost_and_observability_is_next(
    settings, runtime_settings
) -> None:
    app = build(settings, runtime_settings, RecordingObservability())
    classes = [getattr(m, "cls", None) for m in app.user_middleware]
    # Starlette: index 0 is the OUTERMOST user middleware.
    assert classes[0] is RequestContextMiddleware
    assert classes[1] is ProductObservabilityMiddleware
    assert classes.count(ProductObservabilityMiddleware) == 1


def test_every_product_route_is_observed_with_the_server_request_id(
    settings, runtime_settings
) -> None:
    recorder = RecordingObservability()
    with TestClient(build(settings, runtime_settings, recorder, **all_services())) as client:
        responses = exercise(client)
    assert [r.status_code for r in responses] == [200, 200, 403, 201, 200]
    http = recorder.of(P.HTTP_REQUEST)
    assert [r.attributes["http.route"] for r in http] == [route.value for route in ProductRoute]
    for record, response in zip(http, responses, strict=True):
        assert str(record.request_id) == response.headers["X-Request-ID"]
        assert record.attributes["http.status_code"] == response.status_code
        assert record.attributes["http.method"] == response.request.method
    assert [r.outcome for r in http] == [Out.COMPLETED, Out.COMPLETED, Out.DENIED, Out.COMPLETED,
                                         Out.COMPLETED]  # fmt: skip


def test_agentos_and_other_paths_are_not_product_observed(
    settings, runtime_settings, auth_headers
) -> None:
    recorder = RecordingObservability()
    with TestClient(build(settings, runtime_settings, recorder)) as client:
        rejected = client.get("/agents")
        allowed = client.get("/agents", headers=auth_headers)
        others = [client.get("/config", headers=auth_headers), client.get("/nope"),
                  client.get("/health/"), client.get("/api/v1/operations/runs/x")]  # fmt: skip
    assert rejected.status_code == 401 and allowed.status_code == 200
    # X-Request-ID is unchanged on every response, including AgentOS auth rejections.
    for response in (rejected, allowed, *others):
        UUID(response.headers["X-Request-ID"])
    assert recorder.records == []


@pytest.mark.parametrize(
    ("request_args", "services", "status", "outcome"),
    [
        (("GET", "/health", {}), {}, 200, Out.COMPLETED),
        (("POST", RUNS, {"json": {"message": "m", "store_id": STORE}}), {}, 200, Out.COMPLETED),
        (("POST", RUNS, {"json": {"message": "m", "store_id": OTHER_STORE}}), {}, 403, Out.DENIED),
        (("POST", RUNS, {"json": {"message": "m"}}), {}, 422, Out.INVALID),
        (("POST", RUNS, {"json": {"message": "m", "store_id": STORE}}),
         {"operations_service": None}, 503, Out.UNAVAILABLE),
        (("GET", RUNS, {}), {}, 405, Out.INVALID),
        (("GET", REPORT, {"params": {"store_id": STORE, "x": "1"}}), {}, 422, Out.INVALID),
        (("POST", TICKETS, {"json": {"store_id": STORE, "title": "t", "description": "d"},
                            "headers": {"Idempotency-Key": "k"}}), {}, 201, Out.COMPLETED),
        (("POST", TICKETS, {"json": {"store_id": STORE, "title": "t", "description": "d"},
                            "headers": {"Idempotency-Key": "k"}}),
         {"operations_ticket_service": Tickets(ticket_result(replayed=True))}, 200, Out.COMPLETED),
        (("POST", TICKETS, {"json": {"store_id": STORE, "title": "t", "description": "d"},
                            "headers": {"Idempotency-Key": "k"}}),
         {"operations_ticket_service": Tickets(ticket_result(
             status=S.AWAITING_APPROVAL, reason=R.APPROVAL_REQUIRED, ticket_id=None))},
         202, Out.COMPLETED),
        (("POST", TICKETS, {"json": {"store_id": STORE, "title": "t", "description": "d"}}),
         {}, 400, Out.INVALID),
        (("POST", TICKETS, {"json": {"store_id": STORE, "title": "t", "description": "d"},
                            "headers": {"Idempotency-Key": "k"}}),
         {"operations_ticket_service": Tickets(raises=IdempotencyConflictError())},
         409, Out.CONFLICT),
        (("GET", COMMANDS, {"params": {"command_id": str(COMMAND)}}),
         {"operations_ticket_query_service": Queries(raises=TicketCommandNotFoundError())},
         404, Out.NOT_FOUND),
        (("GET", COMMANDS, {"params": {"command_id": "not-a-uuid"}}), {}, 422, Out.INVALID),
    ],
)  # fmt: skip
def test_http_status_outcomes(settings, runtime_settings, request_args, services, status,
                              outcome) -> None:  # fmt: skip
    recorder = RecordingObservability()
    app = build(settings, runtime_settings, recorder, **all_services(**services))
    method, path, kwargs = request_args
    with TestClient(app) as client:
        response = client.request(method, path, **kwargs)
    assert response.status_code == status
    (record,) = recorder.of(P.HTTP_REQUEST)
    assert record.outcome is outcome
    assert record.attributes == {"http.method": method, "http.route": path,
                                 "http.status_code": status}  # fmt: skip


def test_unauthenticated_product_request_is_observed_as_denied(settings, runtime_settings) -> None:
    recorder = RecordingObservability()
    app = build(settings, runtime_settings, recorder, who=None, **all_services())
    with TestClient(app) as client:
        response = client.post(RUNS, json={"message": "m", "store_id": STORE})
    assert response.status_code == 401
    ((record),) = recorder.of(P.HTTP_REQUEST)
    assert record.outcome is Out.DENIED and record.attributes["http.status_code"] == 401
    assert recorder.of(P.OPERATIONS_AGENT_RUN) == []  # the service was never reached


# ----- Correlation: HTTP and service observations share the server request id --------------


@pytest.mark.parametrize(
    ("path", "operation"),
    [(RUNS, P.OPERATIONS_AGENT_RUN), (TICKETS, P.TICKET_COMMAND)],
)
def test_http_and_service_logs_share_the_request_id(settings, runtime_settings, path,
                                                    operation) -> None:  # fmt: skip
    h = harness()
    app = build(settings, runtime_settings, h.observability, **all_services())
    with TestClient(app) as client:
        if path == RUNS:
            response = client.post(RUNS, json={"message": MESSAGE, "store_id": STORE})
        else:
            response = client.post(TICKETS, headers={"Idempotency-Key": IDEMPOTENCY_KEY},
                                   json={"store_id": STORE, "title": TITLE,
                                         "description": DESCRIPTION})  # fmt: skip
    assert response.status_code in (200, 201)
    header = response.headers["X-Request-ID"]
    service_log, http_log = h.logs()  # the service finishes inside the HTTP request
    assert (service_log["operation"], http_log["operation"]) == (operation.value, "http.request")
    assert service_log["request_id"] == http_log["request_id"] == header

    # Same trace, distinct spans, and the HTTP span is the parent of the service span.
    service_span, http_span = h.finished_spans()
    assert ctx(service_span).trace_id == ctx(http_span).trace_id
    assert ctx(service_span).span_id != ctx(http_span).span_id
    assert parent(service_span).span_id == ctx(http_span).span_id
    assert (
        service_log["trace_id"] == http_log["trace_id"] == format(ctx(http_span).trace_id, "032x")
    )
    assert http_span.name == "product.http.request"


# ----- Data minimization over HTTP ---------------------------------------------------------


def test_no_secret_or_business_payload_reaches_telemetry(
    settings, runtime_settings, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level("DEBUG")
    h = harness(product_logger=True)  # completion logs go to the real Product logger
    configured = deployment_settings(
        settings, environment="test",
        product_api_keys=(principal(PRODUCT_KEY, store_ids=frozenset({STORE}),
                                    permissions=frozenset({"tickets.create"})),),
    )  # fmt: skip
    services = all_services(
        operations_service=Runs(raises=RuntimeError("OBS-EXCEPTION-run-failure")),
        daily_operations_service=Reports(raises=RuntimeError("OBS-EXCEPTION-report")),
    )
    app = create_app(configured, runtime_settings, observability=h.observability, **services)
    key_header = {"Authorization": f"Bearer {PRODUCT_KEY}"}
    with TestClient(app) as client:
        responses = [
            client.post(RUNS, json={"message": MESSAGE, "store_id": STORE}, headers=key_header),
            client.get(
                REPORT,
                params={"store_id": STORE, "business_date": "2031-07-19"},
                headers=key_header,
            ),  # fmt: skip
            client.post(
                TICKETS,
                json={"store_id": STORE, "title": TITLE, "description": DESCRIPTION},
                headers={**key_header, "Idempotency-Key": IDEMPOTENCY_KEY},
            ),  # fmt: skip
            client.get(COMMANDS, params={"command_id": str(COMMAND)}, headers=key_header),
            client.post(
                RUNS,
                json={"message": MESSAGE, "store_id": STORE},
                headers={"Authorization": AUTHORIZATION},
            ),  # fmt: skip
        ]
    assert [r.status_code for r in responses] == [503, 503, 201, 200, 401]
    completion = [json.loads(r.getMessage()) for r in caplog.records
                  if r.name == "app.product.observability"]  # fmt: skip
    assert len(completion) == 5 + 4  # every HTTP request, and every reached service

    telemetry = json.dumps(completion) + repr([attrs(s) for s in h.finished_spans()])
    telemetry += repr(h.metric_points())
    # Every record the Product itself logged (the test client's own httpx lines excluded).
    product_records = [r for r in caplog.records if r.name.startswith("app")]
    product_logs = " ".join(r.getMessage() for r in product_records)
    forbidden = [
        "OBS-SECRET", "OBS-IDEMPOTENCY", "OBS-AUTH", "OBS-EXCEPTION", "Bearer",
        str(COMMAND), str(TICKET), STORE, COMPANY, "2031-07-19", WRITER.actor_id,
        "test-api-client", "tickets.create", "operations",  # permissions and role
    ]  # fmt: skip
    for marker in forbidden[:-1]:
        assert marker not in telemetry, marker
        assert marker not in product_logs, marker
    # Only the fixed operation names mention "operations"; no role id leaks.
    assert '"operations"' not in telemetry


def test_metric_attributes_are_bounded_over_http(settings, runtime_settings) -> None:
    h = harness()
    with TestClient(build(settings, runtime_settings, h.observability, **all_services())) as c:
        exercise(c)
    allowed = {
        "operation",
        "outcome",
        "http.method",
        "http.route",
        "http.status_code",
        "business_status",
        "business_reason",
        "replayed",
        "persistence_complete",
    }
    points = h.metric_points()
    assert set(points) == {"product.operation.count", "product.operation.duration"}
    for name, entries in points.items():
        for attributes, _ in entries:
            assert set(attributes) <= allowed, (name, attributes)
            for value in attributes.values():
                assert not (isinstance(value, str) and len(value) == 36 and value.count("-") == 4)


def test_trace_attributes_are_bounded_over_http(settings, runtime_settings) -> None:
    h = harness()
    with TestClient(build(settings, runtime_settings, h.observability, **all_services())) as c:
        exercise(c)
    allowed = {"operation", "outcome", "request_id", "http.method", "http.route",
               "http.status_code", "business_status", "business_reason", "replayed",
               "persistence_complete"}  # fmt: skip
    for span in h.finished_spans():
        assert set(attrs(span)) <= allowed, span.name
        assert list(span.events) == []


# ----- Observability failure never changes Product behaviour -------------------------------


def _snapshot(responses):
    out = []
    for response in responses:
        body = response.json()
        body.pop("request_id", None)
        out.append((response.status_code, body))
    return out


@pytest.mark.parametrize("where", FAILURE_POINTS)
def test_failing_observability_leaves_http_behaviour_unchanged(
    settings, runtime_settings, where
) -> None:
    baseline_services = all_services()
    with TestClient(build(settings, runtime_settings, RecordingObservability(),
                          **baseline_services)) as client:  # fmt: skip
        expected = _snapshot(exercise(client))

    services = all_services()
    failing = FailingObservability(where)
    with TestClient(build(settings, runtime_settings, failing, **services)) as client:
        responses = exercise(client)
    assert _snapshot(responses) == expected
    for response in responses:
        UUID(response.headers["X-Request-ID"])
    # Each service ran exactly once (no retry); observability was attempted.
    assert [
        services[k].calls
        for k in (
            "operations_service",
            "daily_operations_service",
            "operations_ticket_service",
            "operations_ticket_query_service",
        )
    ] == [1, 1, 1, 1]
    assert failing.calls == 5 + 4


# ----- Responses carry no telemetry; health is unchanged -----------------------------------


def test_product_responses_have_no_observability_fields(settings, runtime_settings) -> None:
    h = harness()
    with TestClient(build(settings, runtime_settings, h.observability, **all_services())) as c:
        health, run_response, _, ticket, command = exercise(c)
    assert set(run_response.json()) == {"request_id", "message"}
    assert set(ticket.json()) == {"request_id", "command_id", "status", "reason", "ticket_id",
                                  "replayed", "persistence_complete"}  # fmt: skip
    assert set(command.json()) == {"request_id", "command_id", "status", "reason", "ticket_id",
                                   "created_at", "updated_at"}  # fmt: skip
    assert health.json() == {
        "status": "ok",
        "application": {"name": settings.name, "version": "0.1.0",
                        "environment": settings.environment},
        "agent_runtime": health.json()["agent_runtime"],
    }  # fmt: skip
    assert set(health.json()["agent_runtime"]) == {"framework", "version", "status"}
    for response in (health, run_response, ticket, command):
        for header in response.headers:
            assert "trace" not in header.lower() and "span" not in header.lower()


def test_default_observability_is_used_when_none_is_injected(settings, runtime_settings) -> None:
    services = all_services()
    app = create_app(settings, runtime_settings, actor_resolver=StaticActorResolver(WRITER),
                     **services)  # fmt: skip
    with TestClient(app) as client:
        assert [r.status_code for r in exercise(client)] == [200, 200, 403, 201, 200]


def test_report_forbidden_and_invalid_key_http_mappings_are_unchanged(
    settings, runtime_settings
) -> None:
    recorder = RecordingObservability()
    services = all_services(
        operations_ticket_service=Tickets(raises=InvalidIdempotencyKeyError()),
    )
    with TestClient(build(settings, runtime_settings, recorder, **services)) as client:
        report = client.get(REPORT, params={"store_id": STORE, "business_date": str(date.today())})
        ticket = client.post(TICKETS, json={"store_id": STORE, "title": "t", "description": "d"},
                             headers={"Idempotency-Key": "bad"})  # fmt: skip
    assert (report.status_code, report.json()) == (403, {"detail": "Forbidden"})
    assert (ticket.status_code, ticket.json()) == (400, {"detail": "Invalid Idempotency-Key"})
    assert [r.outcome for r in recorder.of(P.DAILY_REPORT)] == [Out.DENIED]
    assert [r.outcome for r in recorder.of(P.TICKET_COMMAND)] == [Out.INVALID]
