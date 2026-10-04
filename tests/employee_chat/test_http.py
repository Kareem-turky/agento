"""Employee Chat HTTP API (Task 042): Product authentication only (the AgentOS key is
rejected), fixed paths, strict bodies, exactly one Idempotency-Key for a confirmation,
fixed error answers that never echo submitted values, 503 without a repository, and the
route never imports Agno. Real routes and service; in-memory repository and fakes."""

import ast
import socket
from pathlib import Path
from uuid import uuid4

import pytest
from agno.os.settings import AgnoAPISettings
from fastapi.testclient import TestClient

import app.routes.chat as chat_routes
from app.employee_chat.models import ProposedTicket
from app.main import create_app
from tests.conftest import TEST_OS_SECURITY_KEY
from tests.support.chat_fakes import (
    COMPANY,
    OTHER_STORE,
    STORE,
    InMemoryChatRepository,
    RecordingTickets,
    ScriptedChatRunner,
)
from tests.support.product_auth import deployment_settings, principal

EMPLOYEE = "test-chat-employee-key-" + "e" * 24
COLLEAGUE = "test-chat-colleague-key-" + "c" * 24
THREADS, THREAD, TURNS = "/api/v1/chat/threads", "/api/v1/chat/thread", "/api/v1/chat/turns"
CONFIRM = "/api/v1/chat/ticket-proposals/confirm"
CANCEL = "/api/v1/chat/ticket-proposals/cancel"
MARKER = "chat-http-marker-91x"


def auth(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(_socket: object, address: object) -> None:
        raise AssertionError("unexpected outbound connection")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", refuse)


def build(settings, *, repository: bool = True, proposal: bool = True):
    keys = (
        principal(EMPLOYEE, key_id="employee", actor_id="employee",
                  permissions=frozenset({"tickets.create"}), store_ids=frozenset({STORE})),
        principal(COLLEAGUE, key_id="colleague", actor_id="colleague",
                  permissions=frozenset({"tickets.create"}), store_ids=frozenset({STORE})),
    )  # fmt: skip
    configured = deployment_settings(settings, "test", product_api_keys=keys, company_id=COMPANY)
    runner = ScriptedChatRunner(
        reply="Ticket prepared. Confirm the action to create it.",
        proposal=ProposedTicket(title="T", description="D") if proposal else None,
    )
    tickets = RecordingTickets()
    app = create_app(
        configured, AgnoAPISettings(os_security_key=TEST_OS_SECURITY_KEY),
        operations_ticket_service=tickets,  # type: ignore[arg-type]
        chat_repository=InMemoryChatRepository() if repository else None,
        operations_chat_service=runner,
    )  # fmt: skip
    return app, runner, tickets


def thread(client: TestClient) -> str:
    created = client.post(THREADS, headers=auth(EMPLOYEE), json={"store_id": STORE})
    assert created.status_code == 201, created.text
    return created.json()["thread"]["thread_id"]


def test_product_auth_only(settings) -> None:
    app, _, _ = build(settings)
    with TestClient(app) as client:
        for method, path in (("get", THREADS), ("post", THREADS), ("get", THREAD),
                             ("get", TURNS), ("post", TURNS), ("post", CONFIRM),
                             ("post", CANCEL)):  # fmt: skip
            call = getattr(client, method)
            assert call(path).status_code == 401
            assert call(path, headers=auth(TEST_OS_SECURITY_KEY)).status_code == 401


def test_thread_and_turn_flow(settings) -> None:
    app, runner, tickets = build(settings)
    with TestClient(app) as client:
        assert (
            client.post(THREADS, headers=auth(EMPLOYEE), json={"store_id": OTHER_STORE}).status_code
            == 403
        )
        thread_id = thread(client)
        listed = client.get(THREADS, headers=auth(EMPLOYEE), params={"store_id": STORE})
        assert [t["thread_id"] for t in listed.json()["threads"]] == [thread_id]
        assert runner.runs == []  # no model call on create / list
        turn_id = str(uuid4())
        body = {"thread_id": thread_id, "turn_id": turn_id, "message": "  hello\nworld  "}
        first = client.post(TURNS, headers=auth(EMPLOYEE), json=body)
        assert first.status_code == 201
        assert first.json()["turn"]["user_text"] == "hello\nworld"  # trimmed, newlines kept
        assert first.json()["proposal"]["action"] == "operations.ticket.create"
        replay = client.post(TURNS, headers=auth(EMPLOYEE), json=body)
        assert replay.status_code == 200 and replay.json()["replayed"] is True
        assert len(runner.runs) == 1
        conflict = client.post(TURNS, headers=auth(EMPLOYEE),
                               json={**body, "message": MARKER})  # fmt: skip
        assert conflict.status_code == 409 and MARKER not in conflict.text
        for path in (THREAD, TURNS):
            got = client.get(path, headers=auth(EMPLOYEE), params={"thread_id": thread_id})
            assert got.status_code == 200 and len(got.json()["turns"]) == 1
            foreign = client.get(path, headers=auth(COLLEAGUE), params={"thread_id": thread_id})
            assert foreign.status_code == 404
            assert foreign.json() == {"detail": "Chat thread not found"}
        assert tickets.calls == []


@pytest.mark.parametrize(
    "body",
    [
        {"message": ""},
        {"message": "x" * 8001},
        {"message": ["x"]},
        {"message": MARKER, "store_id": STORE},
        {"message": MARKER, "agent_id": "cx"},
    ],
)
def test_strict_turn_bodies_never_echo(settings, body) -> None:
    app, runner, _ = build(settings)
    with TestClient(app) as client:
        thread_id = thread(client)
        answer = client.post(
            TURNS,
            headers=auth(EMPLOYEE),
            json={"thread_id": thread_id, "turn_id": str(uuid4()), **body},
        )
        assert answer.status_code == 422
        assert MARKER not in answer.text and "xxxx" not in answer.text
        assert runner.runs == []


def test_confirm_requires_exactly_one_key_and_only_the_proposal_id(settings) -> None:
    app, _, tickets = build(settings)
    with TestClient(app) as client:
        thread_id = thread(client)
        proposal = client.post(
            TURNS,
            headers=auth(EMPLOYEE),
            json={"thread_id": thread_id, "turn_id": str(uuid4()), "message": "ticket"},
        ).json()
        proposal_id = proposal["proposal"]["proposal_id"]
        missing = client.post(CONFIRM, headers=auth(EMPLOYEE), json={"proposal_id": proposal_id})
        assert missing.status_code == 400
        assert missing.json() == {"detail": "Idempotency-Key required"}
        twice = client.post(
            CONFIRM,
            headers=[*auth(EMPLOYEE).items(), ("Idempotency-Key", "a"), ("Idempotency-Key", "a")],
            json={"proposal_id": proposal_id},
        )
        assert twice.status_code == 400
        extra = client.post(
            CONFIRM,
            headers=auth(EMPLOYEE) | {"Idempotency-Key": "k1"},
            json={"proposal_id": proposal_id, "description": MARKER},
        )
        assert extra.status_code == 422 and MARKER not in extra.text
        assert tickets.calls == []
        done = client.post(
            CONFIRM,
            headers=auth(EMPLOYEE) | {"Idempotency-Key": "k1"},
            json={"proposal_id": proposal_id},
        )
        assert done.status_code == 201
        assert done.json()["ticket"]["status"] == "verified"
        assert done.json()["proposal"]["state"] == "submitted"
        again = client.post(
            CONFIRM,
            headers=auth(EMPLOYEE) | {"Idempotency-Key": "k1"},
            json={"proposal_id": proposal_id},
        )
        assert again.status_code == 200 and again.json()["ticket"]["replayed"] is True
        assert (
            client.post(
                CANCEL, headers=auth(EMPLOYEE), json={"proposal_id": proposal_id}
            ).status_code
            == 409
        )
        assert [c["key"] for c in tickets.calls] == ["k1", "k1"]
        assert [c["title"] for c in tickets.calls] == ["T", "T"]


def test_disabled_agent_is_409_and_unavailable_is_503(settings) -> None:
    app, runner, _ = build(settings)
    with TestClient(app) as client:
        thread_id = thread(client)
        runner.disabled = True
        refused = client.post(
            TURNS,
            headers=auth(EMPLOYEE),
            json={"thread_id": thread_id, "turn_id": str(uuid4()), "message": "hi"},
        )
        assert refused.status_code == 409
        assert refused.json() == {"detail": "Operations Agent is disabled"}
    app, _, _ = build(settings, repository=False)
    with TestClient(app) as client:
        answer = client.post(THREADS, headers=auth(EMPLOYEE), json={"store_id": STORE})
        assert answer.status_code == 503
        assert answer.json() == {"detail": "Employee chat unavailable"}


def test_the_route_never_imports_agno_or_an_agent() -> None:
    tree = ast.parse(Path(chat_routes.__file__).read_text())
    modules = {n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    modules |= {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    assert not [
        m
        for m in modules
        if m.startswith(("agno", "app.agents", "app.persistence", "app.commands", "app.execution"))
    ]
    service = Path(chat_routes.__file__).parents[1] / "employee_chat" / "service.py"
    tree = ast.parse(service.read_text())
    modules = {n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    assert not [m for m in modules if m.startswith(("agno", "app.agents", "app.persistence"))]
