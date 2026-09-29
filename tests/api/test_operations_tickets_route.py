"""POST /api/v1/operations/tickets: transport behaviour with a fake product service.

Real FastAPI app, RequestContextMiddleware and AgentOS auth layer; a test
ActorResolver and a recording fake OperationsTicketCommandService. The real
command/PostgreSQL path is covered in tests/integration/test_ticket_http_postgres.py.
"""

from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from app.governance import ActionScope
from app.main import create_app
from app.routes.operations_tickets import OPERATIONS_TICKETS_PATH, http_status
from app.services import operations_tickets as product
from app.services.operations_tickets import ProductTicketCommandResult
from tests.agents.helpers import COMPANY, OTHER_STORE, STORE, actor
from tests.support.actor_resolver import StaticActorResolver

PATH = "/api/v1/operations/tickets"
S, R = product.TicketCommandStatus, product.TicketCommandReason
KEY = "SENSITIVE-idem-KEY-0001"
TITLE, DESCRIPTION = "Failed delivery", "Shipment requires operations follow-up."
SPOOFED_REQUEST_ID = "attacker-controlled-id"
TICKET = UUID("0b0b0b0b-0000-4000-8000-000000000001")
WRITER = actor(permissions=frozenset({"tickets.create"}))


def result(**overrides) -> ProductTicketCommandResult:
    data = {"command_id": UUID(int=9), "status": S.VERIFIED, "reason": R.VERIFIED,
            "ticket_id": TICKET, "replayed": False, "persistence_complete": True}  # fmt: skip
    return ProductTicketCommandResult(**(data | overrides))


class FakeTicketService:
    def __init__(self, returns=None, raises: BaseException | None = None) -> None:
        self.returns = returns if returns is not None else result()
        self.raises = raises
        self.calls: list[tuple] = []

    async def create_ticket(self, request, scope, title, description, idempotency_key):
        self.calls.append((request, scope, title, description, idempotency_key))
        if self.raises is not None:
            raise self.raises
        return self.returns


def body(**overrides):
    return {"store_id": STORE, "title": TITLE, "description": DESCRIPTION} | overrides


def build(settings, runtime_settings, service=None, who=WRITER, resolver=None):
    return create_app(
        settings,
        runtime_settings,
        actor_resolver=resolver or StaticActorResolver(who),
        operations_ticket_service=service,
    )


def post(app, payload, key: str | None = KEY, headers=None):
    headers = dict(headers or {})
    if key is not None:
        headers["Idempotency-Key"] = key
    with TestClient(app) as client:
        return client.post(PATH, json=payload, headers=headers)


# ----- happy path and response contract ----------------------------------------------------


def test_verified_command_is_201_with_the_safe_response(settings, runtime_settings) -> None:
    service = FakeTicketService()
    response = post(build(settings, runtime_settings, service), body(),
                    headers={"X-Request-ID": SPOOFED_REQUEST_ID})  # fmt: skip
    assert response.status_code == 201
    header = response.headers["X-Request-ID"]
    assert header != SPOOFED_REQUEST_ID
    assert response.json() == {
        "request_id": header, "command_id": str(UUID(int=9)), "status": "verified",
        "reason": "verified", "ticket_id": str(TICKET), "replayed": False,
        "persistence_complete": True,
    }  # fmt: skip
    ((req, scope, title, description, key),) = service.calls
    assert str(req.request_id) == header and req.actor == WRITER
    assert scope == ActionScope(company_id=COMPANY, store_id=STORE)
    assert (title, description, key) == (TITLE, DESCRIPTION, KEY)
    assert KEY not in response.text


def test_title_and_description_are_trimmed_before_the_service(settings, runtime_settings) -> None:
    service = FakeTicketService()
    post(build(settings, runtime_settings, service), body(title="  T  ", description=" D "))
    assert service.calls[0][2:4] == ("T", "D")


@pytest.mark.parametrize(
    ("fields", "code"),
    [
        ({}, 201),
        ({"replayed": True}, 200),
        ({"status": S.IN_PROGRESS, "reason": None, "ticket_id": None, "replayed": True}, 202),
        ({"status": S.AWAITING_APPROVAL, "reason": R.APPROVAL_REQUIRED, "ticket_id": None}, 202),
        ({"status": S.REQUIRES_HUMAN, "reason": R.EXECUTION_OUTCOME_UNCERTAIN,
          "ticket_id": None}, 202),
        ({"status": S.REQUIRES_HUMAN, "reason": R.COMMAND_EXECUTION_ERROR, "ticket_id": None,
          "replayed": True}, 202),
        ({"status": S.REQUIRES_HUMAN, "reason": R.COMMAND_PERSISTENCE_INCOMPLETE,
          "ticket_id": None, "persistence_complete": False}, 202),
        ({"status": S.DENIED, "reason": R.POLICY_DENIED, "ticket_id": None}, 200),
        ({"status": S.DENIED, "reason": R.POLICY_DENIED, "ticket_id": None, "replayed": True},
         200),
        ({"status": S.FAILED, "reason": R.INPUT_INVALID, "ticket_id": None}, 200),
    ],
)  # fmt: skip
def test_http_status_mapping(settings, runtime_settings, fields, code) -> None:
    outcome = result(**fields)
    assert http_status(outcome) == code
    response = post(build(settings, runtime_settings, FakeTicketService(outcome)), body())
    assert response.status_code == code
    data = response.json()
    assert data["status"] == outcome.status.value
    assert data["ticket_id"] == (str(TICKET) if outcome.status is S.VERIFIED else None)


# ----- authentication and AgentOS separation ---------------------------------------------------


def test_no_actor_is_401_even_with_the_agentos_key(
    settings, runtime_settings, auth_headers
) -> None:
    service = FakeTicketService()
    app = create_app(settings, runtime_settings, operations_ticket_service=service)
    response = post(app, body(), headers=auth_headers)
    assert response.status_code == 401 and response.headers["X-Request-ID"]
    assert service.calls == []


def test_unauthenticated_beats_body_and_header_validation(settings, runtime_settings) -> None:
    service = FakeTicketService()
    app = create_app(settings, runtime_settings, operations_ticket_service=service)
    assert post(app, {"action_name": "x"}, key=None).status_code == 401


def test_trusted_actor_needs_no_agentos_key_and_agentos_stays_protected(
    settings, runtime_settings, auth_headers
) -> None:
    app = build(settings, runtime_settings, FakeTicketService())
    with TestClient(app) as client:
        ok = client.post(PATH, json=body(), headers={"Idempotency-Key": KEY})
        assert ok.status_code == 201  # no Authorization header
        assert client.get("/agents").status_code == 401
        assert client.get("/agents", headers=auth_headers).status_code == 200
        agent_ids = {a["id"] for a in client.get("/agents", headers=auth_headers).json()}
    assert "operations" not in agent_ids


def test_the_agentos_exemption_is_exact(settings, runtime_settings) -> None:
    app = build(settings, runtime_settings, FakeTicketService())
    excluded = app.state.agent_os.authorization_config.excluded_route_paths
    assert sorted(excluded) == ["/api/v1/operations/runs", "/api/v1/operations/tickets"]
    assert OPERATIONS_TICKETS_PATH == PATH


# ----- store scope -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("store_ids", "requested"),
    [
        (frozenset({STORE}), OTHER_STORE),
        (frozenset(), STORE),
        (frozenset({"*"}), STORE),
        (frozenset({"all"}), STORE),
        (frozenset({"stores.*"}), STORE),
        (frozenset({STORE.upper()}), STORE),
        (frozenset({STORE}), "00000000-0000-4000-8000-000000000404"),
    ],
)
def test_ungranted_store_is_403_before_the_service(
    settings, runtime_settings, store_ids, requested
) -> None:
    service = FakeTicketService()
    who = actor(permissions=frozenset({"tickets.create"}), store_ids=store_ids)
    response = post(build(settings, runtime_settings, service, who), body(store_id=requested))
    assert response.status_code == 403 and response.json() == {"detail": "Forbidden"}
    assert service.calls == []


def test_company_always_comes_from_the_actor(settings, runtime_settings) -> None:
    service = FakeTicketService()
    other = actor(permissions=frozenset({"tickets.create"}), company_id="company-from-actor")
    post(build(settings, runtime_settings, service, other), body())
    assert service.calls[0][1].company_id == "company-from-actor"


# ----- body validation and spoofing ------------------------------------------------------------


@pytest.mark.parametrize(
    "payload",
    [
        body(title=""), body(title="   "), body(title="x" * 161),
        body(description=""), body(description="  "), body(description="x" * 4001),
        body(store_id="not-a-uuid"), body(title=123), body(description=["x"]),
        {"store_id": STORE, "title": TITLE}, {"title": TITLE, "description": DESCRIPTION},
        body(unknown="x"),
    ],
)  # fmt: skip
def test_invalid_bodies_are_422_and_never_reach_the_service(
    settings, runtime_settings, payload
) -> None:
    service = FakeTicketService()
    assert post(build(settings, runtime_settings, service), payload).status_code == 422
    assert service.calls == []


@pytest.mark.parametrize(
    "field",
    ["action_name", "company_id", "actor_id", "actor_type", "role_ids", "permissions",
     "store_ids", "risk", "required_permission", "requested_write_actions", "allow_write",
     "approved", "approval", "idempotency_key", "command_id", "action_run_id", "run_context",
     "user_id", "session_id"],
)  # fmt: skip
def test_identity_action_and_write_spoofing_fields_are_rejected(
    settings, runtime_settings, field
) -> None:
    service = FakeTicketService()
    response = post(build(settings, runtime_settings, service), body(**{field: "x"}))
    assert response.status_code == 422 and service.calls == []


# ----- Idempotency-Key -------------------------------------------------------------------------


def test_missing_idempotency_key_is_400(settings, runtime_settings) -> None:
    service = FakeTicketService()
    response = post(build(settings, runtime_settings, service), body(), key=None)
    assert response.status_code == 400
    assert response.json() == {"detail": "Idempotency-Key required"}
    assert service.calls == []


def test_repeated_idempotency_key_header_is_400(settings, runtime_settings) -> None:
    service = FakeTicketService()
    app = build(settings, runtime_settings, service)
    with TestClient(app) as client:
        response = client.post(
            PATH, json=body(), headers=[("Idempotency-Key", "a"), ("Idempotency-Key", "b")]
        )
    assert response.status_code == 400 and service.calls == []


def test_key_is_passed_unchanged_and_case_sensitively(settings, runtime_settings) -> None:
    service = FakeTicketService()
    app = build(settings, runtime_settings, service)
    post(app, body(), key="AbC-123")
    post(app, body(), key="abc-123")
    assert [c[4] for c in service.calls] == ["AbC-123", "abc-123"]


def test_invalid_idempotency_key_is_400_without_detail(settings, runtime_settings) -> None:
    service = FakeTicketService(raises=product.InvalidIdempotencyKeyError())
    response = post(build(settings, runtime_settings, service), body(), key="bad key!")
    assert response.status_code == 400
    assert response.json() == {"detail": "Invalid Idempotency-Key"}
    assert "bad key" not in response.text


def test_idempotency_conflict_is_409(settings, runtime_settings) -> None:
    service = FakeTicketService(raises=product.IdempotencyConflictError())
    response = post(build(settings, runtime_settings, service), body())
    assert response.status_code == 409
    assert response.json() == {"detail": "Idempotency conflict"}
    assert KEY not in response.text and TITLE not in response.text


# ----- service availability and fail-closed results -----------------------------------------------


def test_unconfigured_service_is_503(settings, runtime_settings) -> None:
    response = post(build(settings, runtime_settings, None), body())
    assert response.status_code == 503
    assert response.json() == {"detail": "Operations ticket service unavailable"}


def test_non_service_object_counts_as_unconfigured(settings, runtime_settings) -> None:
    response = post(build(settings, runtime_settings, object()), body())  # type: ignore[arg-type]
    assert response.status_code == 503


@pytest.mark.parametrize(
    "error",
    [
        product.TicketCommandUnavailableError(),
        RuntimeError("psql://admin:hunter2@db.internal:5432 SENSITIVE-PROVIDER tkt_9"),
        ValueError("SENSITIVE-PROVIDER"),
    ],
)
def test_service_exceptions_are_a_safe_503(settings, runtime_settings, error) -> None:
    response = post(build(settings, runtime_settings, FakeTicketService(raises=error)), body())
    assert response.status_code == 503
    assert response.json() == {"detail": "Operations ticket service unavailable"}
    assert response.headers["X-Request-ID"]
    for secret in ("hunter2", "db.internal", "SENSITIVE", "tkt_9", KEY):
        assert secret not in response.text


class Misleading:
    status = "verified"
    ticket_id = "FAKE-TICKET-ID"
    command_id = uuid4()


@pytest.mark.parametrize(
    "bad",
    [None, {"status": "verified", "ticket_id": "FAKE-TICKET-ID"}, "verified", Misleading(),
     result().model_dump()],
)  # fmt: skip
def test_invalid_service_results_fail_closed(settings, runtime_settings, bad) -> None:
    service = FakeTicketService()
    service.returns = bad
    response = post(build(settings, runtime_settings, service), body())
    assert response.status_code == 503
    assert "FAKE-TICKET-ID" not in response.text and "Misleading" not in response.text
