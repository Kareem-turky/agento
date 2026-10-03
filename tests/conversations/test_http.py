"""Conversation HTTP API (Task 037): Product authentication only (the AgentOS key is
rejected), conversations.read, 404 for foreign/inaccessible ids, bounded pagination,
values never echoed, fixed READ-ONLY paths (no ingest, webhook or send route), 503
without a service. Real routes and service; in-memory repositories; no network."""

import asyncio
import socket
from uuid import uuid4

import pytest
from agno.os.settings import AgnoAPISettings
from fastapi.testclient import TestClient

from app.main import create_app
from app.routes.conversations import CONVERSATIONS_PATHS
from tests.conftest import TEST_OS_SECURITY_KEY
from tests.routes import effective_api_routes
from tests.support.conversation_fakes import (
    COMPANY,
    INJECTION,
    OTHER_COMPANY,
    SCRIPT,
    STORE_A,
    STORE_B,
    ConversationWorld,
    envelope,
)
from tests.support.product_auth import deployment_settings, principal

READER = "test-conversation-reader-key-" + "r" * 24
OUTSIDER = "test-conversation-outsider-key-" + "o" * 24
BASE = "/api/v1/conversations"
ONE, MESSAGES = f"{BASE}/conversation", f"{BASE}/messages"


def auth(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


def run(coroutine):
    return asyncio.run(coroutine)


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(_socket: object, address: object) -> None:
        raise AssertionError("unexpected outbound connection")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", refuse)


class World:
    def __init__(self, settings, *, with_service: bool = True) -> None:
        self.conversations = ConversationWorld()
        keys = (
            principal(READER, key_id="reader", actor_id="reader",
                      permissions=frozenset({"conversations.read"}),
                      store_ids=frozenset({STORE_A})),
            principal(OUTSIDER, key_id="outsider", actor_id="outsider",
                      permissions=frozenset({"orders.read"})),
        )  # fmt: skip
        configured = deployment_settings(settings, "test", product_api_keys=keys,
                                         company_id=COMPANY)  # fmt: skip
        self.app = create_app(
            configured, AgnoAPISettings(os_security_key=TEST_OS_SECURITY_KEY),
            conversation_service=self.conversations.service if with_service else None,
        )  # fmt: skip

    def seed(self, store: str | None = None, *, company: str = COMPANY, text: str = "Hello",
             ref: str = "m-1", thread: str = "thread-1", conn=None):  # fmt: skip
        world = self.conversations
        conn = conn or run(world.channel(company=company))
        return run(world.ingress.ingest(world.context(conn, store),
                                        envelope(text, ref=ref, thread=thread)))  # fmt: skip


@pytest.fixture
def world(settings) -> World:
    return World(settings)


def test_every_route_requires_product_authentication(world: World) -> None:
    seeded = world.seed()
    cid = {"conversation_id": str(seeded.conversation.conversation_id)}
    with TestClient(world.app) as client:
        for path, params in ((BASE, {}), (ONE, cid), (MESSAGES, cid)):
            for headers in ({}, auth("test-wrong-" + "x" * 40), auth(TEST_OS_SECURITY_KEY)):
                assert client.get(path, params=params, headers=headers).status_code == 401
            response = client.get(path, params=params, headers=auth(OUTSIDER))
            assert (response.status_code, response.json()) == (403, {"detail": "Forbidden"})


def test_authorized_reads_return_safe_views(world: World) -> None:
    seeded = world.seed(text=INJECTION)
    conn = next(iter(world.conversations.connections.rows.values()))
    world.seed(text=SCRIPT, ref="m-2", conn=conn)
    cid = str(seeded.conversation.conversation_id)
    with TestClient(world.app) as client:
        listed = client.get(BASE, headers=auth(READER))
        assert listed.status_code == 200
        (item,) = listed.json()["conversations"]
        assert item["conversation_id"] == cid and item["store_id"] is None
        assert item["channel"]["integration_name"] == "Example Chat (test)"
        assert item["channel"]["connection_name"] == "Support inbox"
        one = client.get(ONE, params={"conversation_id": cid}, headers=auth(READER))
        assert one.status_code == 200 and one.json()["conversation"]["conversation_id"] == cid
        messages = client.get(MESSAGES, params={"conversation_id": cid}, headers=auth(READER))
        body = messages.json()
        assert [m["text"] for m in body["messages"]] == [INJECTION, SCRIPT]  # text, as data
        assert [m["sequence"] for m in body["messages"]] == [1, 2]
        assert body["next_before_sequence"] is None
        first = body["messages"][0]
        assert set(first) == {"message_id", "sequence", "direction", "author_kind",
                              "external_sender_ref", "text", "occurred_at", "recorded_at",
                              "delivery_state"}  # fmt: skip
        assert (first["direction"], first["delivery_state"]) == ("inbound", "received")
        for word in ("fingerprint", "company_id", "connection_secret", "payload", "headers"):
            assert word not in messages.text


def test_foreign_and_inaccessible_store_conversations_are_404(world: World) -> None:
    foreign = world.seed(company=OTHER_COMPANY)
    other_store = world.seed(STORE_B, ref="b-1", thread="t-b")
    mine = world.seed(STORE_A, ref="a-1", thread="t-a")
    with TestClient(world.app) as client:
        ids = [c["conversation_id"] for c in
               client.get(BASE, headers=auth(READER)).json()["conversations"]]  # fmt: skip
        assert ids == [str(mine.conversation.conversation_id)]
        for target in (foreign, other_store):
            params = {"conversation_id": str(target.conversation.conversation_id)}
            for path in (ONE, MESSAGES):
                response = client.get(path, params=params, headers=auth(READER))
                assert (response.status_code, response.json()) == (
                    404,
                    {"detail": "Conversation not found"},
                )
        missing = client.get(ONE, params={"conversation_id": str(uuid4())}, headers=auth(READER))
        assert missing.status_code == 404


def test_bounded_inputs_are_validated_without_echo(world: World) -> None:
    seeded = world.seed()
    cid = str(seeded.conversation.conversation_id)
    with TestClient(world.app) as client:
        for path, params in ((BASE, {"limit": 101}), (BASE, {"limit": 0}),
                             (BASE, {"connection_id": "MARKER-x"}),
                             (MESSAGES, {"conversation_id": cid, "limit": 500}),
                             (MESSAGES, {"conversation_id": cid, "before_sequence": 1}),
                             (MESSAGES, {"conversation_id": "MARKER-not-a-uuid"})):  # fmt: skip
            response = client.get(path, params=params, headers=auth(READER))
            assert response.status_code == 422 and "MARKER" not in response.text, params


def test_routes_are_fixed_read_only_with_no_ingest_or_send(world: World) -> None:
    routes = {(m, p) for m, p in effective_api_routes(world.app.routes)
              if p.startswith(BASE)}  # fmt: skip
    assert routes == {("GET", p) for p in CONVERSATIONS_PATHS}
    with TestClient(world.app) as client:
        for method in ("POST", "PUT", "PATCH", "DELETE"):
            for path in CONVERSATIONS_PATHS:
                response = client.request(method, path, headers=auth(READER), json={})
                assert response.status_code == 405, (method, path)
        for path in (f"{BASE}/inbound", f"{BASE}/webhook", f"{BASE}/send", f"{BASE}/reply",
                     f"{BASE}/messages/send", "/api/v1/webhooks/messages"):  # fmt: skip
            response = client.post(path, headers=auth(READER), json={"text": "hi"})
            assert response.status_code in (401, 404, 405), path
    assert world.conversations.repository.messages == {}


def test_missing_service_is_503(settings) -> None:
    world = World(settings, with_service=False)
    with TestClient(world.app) as client:
        response = client.get(BASE, headers=auth(READER))
        assert (response.status_code, response.json()) == (
            503,
            {"detail": "Conversations unavailable"},
        )
