"""GET /api/v1/operations/tickets/commands: transport behaviour with a fake query service.

Real FastAPI app, RequestContextMiddleware and AgentOS auth layer; a test
ActorResolver and a recording fake OperationsTicketCommandQueryService. The real
PostgreSQL POST->GET path is in tests/integration/test_ticket_command_status_http.py.
"""

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.routes.operations_tickets import OPERATIONS_TICKET_COMMANDS_PATH
from app.services import operations_tickets as product
from app.services.operations_tickets import ProductTicketCommandStatusResult
from tests.agents.helpers import actor
from tests.support.actor_resolver import StaticActorResolver

PATH = "/api/v1/operations/tickets/commands"
S, R = product.TicketCommandStatus, product.TicketCommandReason
TICKET = UUID("0b0b0b0b-0000-4000-8000-000000000001")
COMMAND = UUID("0c0c0c0c-0000-4000-8000-000000000001")
T0 = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)
T1 = datetime(2026, 3, 1, 12, 5, tzinfo=UTC)
READER = actor(permissions=frozenset())


def status_result(**overrides) -> ProductTicketCommandStatusResult:
    data = {"command_id": COMMAND, "status": S.VERIFIED, "reason": R.VERIFIED,
            "ticket_id": TICKET, "created_at": T0, "updated_at": T1}  # fmt: skip
    return ProductTicketCommandStatusResult(**(data | overrides))


class FakeQueryService:
    def __init__(self, returns=None, raises: BaseException | None = None) -> None:
        self.returns = returns if returns is not None else status_result()
        self.raises = raises
        self.calls: list[tuple] = []

    async def get_command(self, request, command_id):
        self.calls.append((request, command_id))
        if self.raises is not None:
            raise self.raises
        return self.returns


class ExplodingTicketService:
    """The write service must never be touched by a status read."""

    async def create_ticket(self, *args, **kwargs):
        raise AssertionError("GET must never reach the write service")


def build(settings, runtime_settings, query=None, who=READER, resolver=None):
    return create_app(
        settings,
        runtime_settings,
        actor_resolver=resolver or StaticActorResolver(who),
        operations_ticket_service=ExplodingTicketService(),
        operations_ticket_query_service=query,
    )


def get(app, command_id=str(COMMAND), headers=None, params=None):
    with TestClient(app) as client:
        query = params if params is not None else {"command_id": command_id}
        return client.get(PATH, params=query, headers=headers or {})


def test_found_command_is_200_with_the_durable_status(settings, runtime_settings) -> None:
    service = FakeQueryService()
    headers = {"X-Request-ID": "spoofed", "Idempotency-Key": "ignored-key"}
    response = get(build(settings, runtime_settings, service), headers=headers)
    assert response.status_code == 200
    header = response.headers["X-Request-ID"]
    assert header != "spoofed"
    assert response.json() == {
        "request_id": header, "command_id": str(COMMAND), "status": "verified",
        "reason": "verified", "ticket_id": str(TICKET),
        "created_at": "2026-03-01T12:00:00Z", "updated_at": "2026-03-01T12:05:00Z",
    }  # fmt: skip
    ((req, command_id),) = service.calls
    assert command_id == COMMAND and str(req.request_id) == header and req.actor == READER
    assert "ignored-key" not in response.text


@pytest.mark.parametrize(
    "fields",
    [
        {"status": S.IN_PROGRESS, "reason": None, "ticket_id": None},
        {"status": S.DENIED, "reason": R.POLICY_DENIED, "ticket_id": None},
        {"status": S.FAILED, "reason": R.INPUT_INVALID, "ticket_id": None},
        {"status": S.AWAITING_APPROVAL, "reason": R.APPROVAL_REQUIRED, "ticket_id": None},
        {"status": S.REQUIRES_HUMAN, "reason": R.EXECUTION_OUTCOME_UNCERTAIN, "ticket_id": None},
    ],
)
def test_every_durable_state_is_200_without_ticket_id(settings, runtime_settings, fields) -> None:
    response = get(build(settings, runtime_settings, FakeQueryService(status_result(**fields))))
    assert response.status_code == 200
    data = response.json()
    assert (data["status"], data["ticket_id"]) == (fields["status"].value, None)
    assert "replayed" not in data and "persistence_complete" not in data


def test_not_found_is_a_generic_404(settings, runtime_settings) -> None:
    service = FakeQueryService(raises=product.TicketCommandNotFoundError())
    response = get(build(settings, runtime_settings, service))
    assert response.status_code == 404
    assert response.json() == {"detail": "Ticket command not found"}


def test_no_actor_is_401_even_with_the_agentos_key(
    settings, runtime_settings, auth_headers
) -> None:
    service = FakeQueryService()
    app = create_app(settings, runtime_settings, operations_ticket_query_service=service)
    response = get(app, headers=auth_headers)
    assert response.status_code == 401 and response.headers["X-Request-ID"]
    assert service.calls == []


def test_trusted_actor_needs_no_agentos_key_and_agentos_stays_protected(
    settings, runtime_settings, auth_headers
) -> None:
    app = build(settings, runtime_settings, FakeQueryService())
    with TestClient(app) as client:
        assert client.get(PATH, params={"command_id": str(COMMAND)}).status_code == 200
        assert client.get("/agents").status_code == 401
        assert client.get("/agents", headers=auth_headers).status_code == 200
    assert OPERATIONS_TICKET_COMMANDS_PATH == PATH
    excluded = app.state.agent_os.authorization_config.excluded_route_paths
    assert sorted(excluded) == [
        "/api/v1/operations/runs",
        "/api/v1/operations/tickets",
        "/api/v1/operations/tickets/commands",
    ]
    assert not [p for p in excluded if any(c in p for c in "*?[{")]


@pytest.mark.parametrize(
    "params",
    [{"command_id": "not-a-uuid"}, {"command_id": ""}, {}, {"command_id": "1234"},
     {"id": str(COMMAND)}],
)  # fmt: skip
def test_invalid_or_missing_command_id_is_422_without_a_service_call(
    settings, runtime_settings, params
) -> None:
    service = FakeQueryService()
    assert get(build(settings, runtime_settings, service), params=params).status_code == 422
    assert service.calls == []


def test_path_style_ids_are_not_routes(settings, runtime_settings) -> None:
    service = FakeQueryService()
    app = build(settings, runtime_settings, service)
    with TestClient(app) as client:
        assert client.get(f"{PATH}/{COMMAND}").status_code in (401, 404, 405)
    assert service.calls == []


def test_unconfigured_query_service_is_503(settings, runtime_settings) -> None:
    response = get(build(settings, runtime_settings, None))
    assert response.status_code == 503
    assert response.json() == {"detail": "Operations ticket query service unavailable"}


@pytest.mark.parametrize(
    "error",
    [
        product.TicketCommandQueryUnavailableError(),
        RuntimeError("psql://admin:hunter2@db.internal:5432 company-SECRET store-SECRET"),
        KeyError("SENSITIVE"),
    ],
)
def test_query_service_exceptions_are_a_safe_503(settings, runtime_settings, error) -> None:
    response = get(build(settings, runtime_settings, FakeQueryService(raises=error)))
    assert response.status_code == 503
    assert response.json() == {"detail": "Operations ticket query service unavailable"}
    assert response.headers["X-Request-ID"]
    for secret in ("hunter2", "db.internal", "SECRET", "SENSITIVE"):
        assert secret not in response.text


class Misleading:
    status = "verified"
    ticket_id = "FAKE-TICKET-ID"
    command_id = uuid4()


@pytest.mark.parametrize(
    "bad",
    [{"status": "verified", "ticket_id": "FAKE-TICKET-ID"}, "verified", Misleading(),
     status_result().model_dump(), 0],
)  # fmt: skip
def test_invalid_query_results_fail_closed(settings, runtime_settings, bad) -> None:
    service = FakeQueryService()
    service.returns = bad
    response = get(build(settings, runtime_settings, service))
    assert response.status_code == 503
    assert "FAKE-TICKET-ID" not in response.text and "Misleading" not in response.text


def test_none_result_fails_closed(settings, runtime_settings) -> None:
    class ReturnsNone:
        async def get_command(self, request, command_id):
            return None

    assert get(build(settings, runtime_settings, ReturnsNone())).status_code == 503
