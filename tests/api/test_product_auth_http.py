"""Product API-key authentication over HTTP with the DEFAULT resolver composition.

create_app(settings) builds the resolver from settings (no actor_resolver is passed):
Product routes authenticate with ``Authorization: Bearer <Product API key>``; AgentOS
routes with ``OS_SECURITY_KEY``. Neither credential opens the other surface.
Narrow fake services observe what the authenticated request carries.
"""

import json
import logging
from datetime import UTC, datetime
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from app.application.operations_tickets import WriteCommandTicketService
from app.commands import WriteCommandCoordinator
from app.config import Settings
from app.execution import ActionHandlerRegistry
from app.governance import ActionCatalog, GovernanceGate
from app.main import create_app
from app.operations import OPERATIONS_ACTIONS, CreateOperationalTicketHandler
from app.services.operations import ProductOperationsRunResult
from app.services.operations_tickets import (
    ProductTicketCommandResult,
    ProductTicketCommandStatusResult,
    TicketCommandReason,
    TicketCommandStatus,
)
from tests.commands.fakes import CountingExecutionCoordinator, InMemoryWriteCommandStore
from tests.conftest import TEST_OS_SECURITY_KEY
from tests.execution.fakes import FIXED_TIME, RecordingAuditSink
from tests.operations.helpers import COMPANY, OTHER_STORE, STORE, stack
from tests.support.product_auth import principal

KEY_A = "test-product-key-A-" + "a" * 30  # obviously test-only
KEY_B = "test-product-key-B-" + "b" * 30
MARKER_KEY = "test-product-key-LEAKMARKER-" + "m" * 20
PRINCIPAL_A = principal(
    KEY_A, key_id="key-a", actor_id="actor-a", role_ids=frozenset({"operations"}),
    permissions=frozenset({"orders.read", "shipments.read", "tickets.create"}),
    store_ids=frozenset({STORE}),
)  # fmt: skip
PRINCIPAL_B = principal(
    KEY_B, key_id="key-b", actor_id="actor-b", role_ids=frozenset({"viewer"}),
    permissions=frozenset({"orders.read"}), store_ids=frozenset({OTHER_STORE}),
)  # fmt: skip
PRINCIPAL_MARKER = principal(
    MARKER_KEY, key_id="key-marker", actor_id="actor-marker",
    permissions=frozenset({"tickets.create"}), store_ids=frozenset({STORE}),
)  # fmt: skip
RUNS, TICKETS, STATUS = (
    "/api/v1/operations/runs", "/api/v1/operations/tickets",
    "/api/v1/operations/tickets/commands",
)  # fmt: skip
T0 = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)
TICKET = UUID("0b0b0b0b-0000-4000-8000-000000000001")
COMMAND = UUID("0c0c0c0c-0000-4000-8000-000000000001")


def bearer(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


def configured(settings: Settings, environment: str = "production", **updates) -> Settings:
    data = settings.model_dump() | {
        "environment": environment,
        "product_auth_mode": "api_key",
        "company_id": COMPANY,
        "product_api_keys": (PRINCIPAL_A, PRINCIPAL_B, PRINCIPAL_MARKER),
    }
    return Settings(_env_file=None, **(data | updates))


class RunService:
    def __init__(self) -> None:
        self.calls: list = []

    async def run_product(self, request, scope, message):
        self.calls.append((request, scope))
        return ProductOperationsRunResult(message="ok")


class TicketService:
    def __init__(self) -> None:
        self.calls: list = []

    async def create_ticket(self, request, scope, title, description, idempotency_key):
        self.calls.append((request, scope))
        return ProductTicketCommandResult(
            command_id=COMMAND, status=TicketCommandStatus.VERIFIED,
            reason=TicketCommandReason.VERIFIED, ticket_id=TICKET, replayed=False,
            persistence_complete=True,
        )  # fmt: skip


class QueryService:
    def __init__(self) -> None:
        self.calls: list = []

    async def get_command(self, request, command_id):
        self.calls.append(request)
        return ProductTicketCommandStatusResult(
            command_id=command_id, status=TicketCommandStatus.VERIFIED,
            reason=TicketCommandReason.VERIFIED, ticket_id=TICKET, created_at=T0, updated_at=T0,
        )  # fmt: skip


class Surface:
    def __init__(self, settings, runtime_settings, **overrides) -> None:
        self.runs, self.tickets, self.queries = RunService(), TicketService(), QueryService()
        self.app = create_app(
            configured(settings, **overrides), runtime_settings,
            operations_service=self.runs, operations_ticket_service=self.tickets,
            operations_ticket_query_service=self.queries,
        )  # fmt: skip
        # No actor_resolver is passed: the default composition builds it from settings.

    def call_all(self, headers: dict[str, str]) -> dict[str, int]:
        with TestClient(self.app) as client:
            return {
                "runs": client.post(RUNS, json={"message": "hi", "store_id": STORE},
                                    headers=headers).status_code,
                "tickets": client.post(TICKETS, json={"store_id": STORE, "title": "t",
                                       "description": "d"},
                                       headers={**headers, "Idempotency-Key": "k-1"}).status_code,
                "status": client.get(STATUS, params={"command_id": str(COMMAND)},
                                     headers=headers).status_code,
                "agents": client.get("/agents", headers=headers).status_code,
                "health": client.get("/health", headers=headers).status_code,
            }  # fmt: skip

    @property
    def service_calls(self) -> int:
        return len(self.runs.calls) + len(self.tickets.calls) + len(self.queries.calls)


def test_default_composition_uses_the_configured_api_key_resolver(
    settings, runtime_settings
) -> None:
    from app.auth import ProductApiKeyActorResolver
    from app.context import RequestContextMiddleware

    surface = Surface(settings, runtime_settings)
    (middleware,) = [m for m in surface.app.user_middleware if m.cls is RequestContextMiddleware]
    assert isinstance(middleware.kwargs["resolver"], ProductApiKeyActorResolver)


def test_product_key_authenticates_product_routes_only(settings, runtime_settings) -> None:
    surface = Surface(settings, runtime_settings)
    codes = surface.call_all(bearer(KEY_A))
    assert codes == {"runs": 200, "tickets": 201, "status": 200, "agents": 401, "health": 200}
    for req, _scope in surface.runs.calls + surface.tickets.calls:
        assert req.actor.model_dump() == {
            "actor_id": "actor-a", "actor_type": "api_client", "company_id": COMPANY,
            "role_ids": frozenset({"operations"}),
            "permissions": frozenset({"orders.read", "shipments.read", "tickets.create"}),
            "store_ids": frozenset({STORE}),
        }  # fmt: skip
    (query_request,) = surface.queries.calls
    assert query_request.actor.actor_id == "actor-a"
    assert surface.runs.calls[0][1].company_id == COMPANY


def test_os_key_authenticates_agentos_only(settings, runtime_settings) -> None:
    surface = Surface(settings, runtime_settings)
    codes = surface.call_all(bearer(TEST_OS_SECURITY_KEY))
    assert codes == {"runs": 401, "tickets": 401, "status": 401, "agents": 200, "health": 200}
    assert surface.service_calls == 0


@pytest.mark.parametrize(
    "headers",
    [
        {},
        bearer("z" * 40),  # well-formed but unknown
        bearer(KEY_A.upper()),  # case-sensitive
        bearer("short"),
        {"Authorization": f"Basic {KEY_A}"},
        {"Authorization": f"Bearer  {KEY_A}"},
        {"Authorization": f"Bearer {KEY_A} {KEY_B}"},
        {"Authorization": KEY_A},
        {"X-Actor-ID": "actor-a", "X-Company-ID": COMPANY, "X-Permissions": "tickets.create",
         "X-Store-Ids": STORE},
        {"Authorization": f"Bearer {PRINCIPAL_A.key_sha256}"},
    ],
    ids=["missing", "unknown", "wrong-case", "malformed", "basic", "double-space",
         "two-credentials", "no-scheme", "identity-headers", "hash-as-key"],
)  # fmt: skip
def test_every_auth_failure_is_the_same_401(settings, runtime_settings, headers) -> None:
    surface = Surface(settings, runtime_settings)
    with TestClient(surface.app) as client:
        responses = [
            client.post(RUNS, json={"message": "hi", "store_id": STORE}, headers=headers),
            client.get(STATUS, params={"command_id": str(COMMAND)}, headers=headers),
        ]
        responses.append(client.get("/agents", headers=headers))
        assert client.get("/health", headers=headers).status_code == 200
    assert [r.status_code for r in responses] == [401, 401, 401]
    assert responses[0].json() == responses[1].json() == {"detail": "Not authenticated"}
    assert surface.service_calls == 0


def test_duplicate_authorization_headers_fail(settings, runtime_settings) -> None:
    surface = Surface(settings, runtime_settings)
    with TestClient(surface.app) as client:
        response = client.get(
            STATUS, params={"command_id": str(COMMAND)},
            headers=[("Authorization", f"Bearer {KEY_A}"), ("Authorization", f"Bearer {KEY_A}")],
        )  # fmt: skip
    assert response.status_code == 401 and surface.service_calls == 0


def test_valid_key_does_not_bypass_store_scope(settings, runtime_settings) -> None:
    surface = Surface(settings, runtime_settings)
    with TestClient(surface.app) as client:
        run = client.post(RUNS, json={"message": "hi", "store_id": OTHER_STORE},
                          headers=bearer(KEY_A))  # fmt: skip
        ticket = client.post(TICKETS, json={"store_id": OTHER_STORE, "title": "t",
                                            "description": "d"},
                             headers={**bearer(KEY_A), "Idempotency-Key": "k-2"})  # fmt: skip
        # Key B is granted OTHER_STORE only; STORE is forbidden to it.
        other = client.post(RUNS, json={"message": "hi", "store_id": STORE},
                            headers=bearer(KEY_B))  # fmt: skip
        own = client.post(RUNS, json={"message": "hi", "store_id": OTHER_STORE},
                          headers=bearer(KEY_B))  # fmt: skip
    assert (run.status_code, ticket.status_code, other.status_code) == (403, 403, 403)
    assert own.status_code == 200
    ((req, scope),) = surface.runs.calls
    assert (req.actor.actor_id, scope.store_id, scope.company_id) == (
        "actor-b", OTHER_STORE, COMPANY,
    )  # fmt: skip
    assert surface.tickets.calls == []


def test_multiple_principals_are_isolated_in_one_company(settings, runtime_settings) -> None:
    surface = Surface(settings, runtime_settings)
    with TestClient(surface.app) as client:
        for key in (KEY_A, KEY_B):
            client.get(STATUS, params={"command_id": str(COMMAND)}, headers=bearer(key))
    a, b = (r.actor for r in surface.queries.calls)
    assert (a.actor_id, a.store_ids, a.role_ids) == ("actor-a", frozenset({STORE}),
                                                     frozenset({"operations"}))  # fmt: skip
    assert (b.actor_id, b.store_ids, b.permissions) == (
        "actor-b", frozenset({OTHER_STORE}), frozenset({"orders.read"}),
    )  # fmt: skip
    assert a.company_id == b.company_id == COMPANY


def test_valid_key_does_not_bypass_governance(settings, runtime_settings) -> None:
    # Key B authenticates, but has no tickets.create: the governed command is DENIED.
    s = stack()
    executor = CountingExecutionCoordinator(
        GovernanceGate(ActionCatalog(OPERATIONS_ACTIONS)),
        ActionHandlerRegistry([CreateOperationalTicketHandler(s.spy)]),
        RecordingAuditSink(), clock=lambda: FIXED_TIME,
    )  # fmt: skip
    service = WriteCommandTicketService(
        WriteCommandCoordinator(
            InMemoryWriteCommandStore(), executor, ActionCatalog(OPERATIONS_ACTIONS)
        )  # fmt: skip
    )
    app = create_app(configured(settings), runtime_settings, operations_ticket_service=service)
    with TestClient(app) as client:
        response = client.post(
            TICKETS, json={"store_id": OTHER_STORE, "title": "t", "description": "d"},
            headers={**bearer(KEY_B), "Idempotency-Key": "governed-1"},
        )  # fmt: skip
    data = response.json()
    assert response.status_code == 200
    assert (data["status"], data["reason"], data["ticket_id"]) == ("denied", "policy_denied", None)
    assert s.desk.ticket_count == 0 and executor.calls == 1


def test_explicit_actor_resolver_override_still_wins(settings, runtime_settings) -> None:
    from tests.agents.helpers import actor
    from tests.support.actor_resolver import StaticActorResolver

    runs = RunService()
    who = actor(store_ids=frozenset({STORE}))
    app = create_app(
        configured(settings),
        runtime_settings,
        actor_resolver=StaticActorResolver(who),
        operations_service=runs,
    )
    with TestClient(app) as client:
        # No key at all: the injected resolver is used exactly, not the configured one.
        assert client.post(RUNS, json={"message": "hi", "store_id": STORE}).status_code == 200
    assert runs.calls[0][0].actor == who


def test_deployments_refuse_to_start_without_product_auth(settings, runtime_settings) -> None:
    from app.auth import ProductAuthConfigurationError

    for environment in ("staging", "production"):
        unvalidated = settings.model_copy(update={"environment": environment})
        with pytest.raises(ProductAuthConfigurationError):
            create_app(unvalidated, runtime_settings)


def test_health_reveals_nothing_about_auth(settings, runtime_settings) -> None:
    surface = Surface(settings, runtime_settings)
    with TestClient(surface.app) as client:
        for headers in ({}, bearer(KEY_A), bearer("z" * 40)):
            response = client.get("/health", headers=headers)
            assert response.status_code == 200
            for secret in ("key-a", "actor-a", COMPANY, STORE, PRINCIPAL_A.key_sha256,
                           "tickets.create", "api_key", "operations"):  # fmt: skip
                assert secret not in response.text


def test_raw_key_never_leaks(settings, runtime_settings, caplog) -> None:
    caplog.set_level(logging.DEBUG)
    surface = Surface(settings, runtime_settings)
    texts: list[str] = []
    with TestClient(surface.app) as client:
        for headers in (bearer(MARKER_KEY), {"Authorization": f"Basic {MARKER_KEY}"}):
            for response in (
                client.post(RUNS, json={"message": "hi", "store_id": STORE}, headers=headers),
                client.post(TICKETS, json={"store_id": STORE, "title": "t", "description": "d"},
                            headers={**headers, "Idempotency-Key": "k-3"}),
                client.get(STATUS, params={"command_id": str(COMMAND)}, headers=headers),
                client.get("/agents", headers=headers),
                client.get("/health", headers=headers),
                client.post(RUNS, json={"bad": True}, headers=headers),  # 422 detail
            ):  # fmt: skip
                texts += [response.text, json.dumps(dict(response.headers))]
    for req, scope in surface.runs.calls + surface.tickets.calls:
        texts += [req.model_dump_json(), repr(req), repr(scope)]
    texts += [repr(r) for r in surface.queries.calls]
    texts += [configured(settings).model_dump_json(), repr(configured(settings))]
    texts += [record.getMessage() for record in caplog.records]
    texts += [caplog.text]
    assert surface.tickets.calls and surface.runs.calls  # the marker key did authenticate
    for text in texts:
        assert MARKER_KEY not in text
        assert "LEAKMARKER" not in text


def test_raw_key_never_reaches_downstream_audit(settings, runtime_settings) -> None:
    s = stack()
    sink = RecordingAuditSink()
    executor = CountingExecutionCoordinator(
        GovernanceGate(ActionCatalog(OPERATIONS_ACTIONS)),
        ActionHandlerRegistry([CreateOperationalTicketHandler(s.spy)]), sink,
        clock=lambda: FIXED_TIME,
    )  # fmt: skip
    service = WriteCommandTicketService(WriteCommandCoordinator(
        InMemoryWriteCommandStore(), executor, ActionCatalog(OPERATIONS_ACTIONS)))  # fmt: skip
    app = create_app(configured(settings), runtime_settings, operations_ticket_service=service)
    with TestClient(app) as client:
        response = client.post(
            TICKETS,
            json={"store_id": STORE, "title": "t", "description": "d"},
            headers={**bearer(MARKER_KEY), "Idempotency-Key": "audit-1"},
        )
    assert response.status_code == 201 and sink.events
    events = json.dumps([e.model_dump(mode="json") for e in sink.events])
    assert MARKER_KEY not in events and "LEAKMARKER" not in events
    assert {e.actor_id for e in sink.events} == {"actor-marker"}
    assert {e.actor_type for e in sink.events} == {"api_client"}
