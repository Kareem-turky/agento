"""POST /api/v1/operations/tickets end to end against real PostgreSQL. No agent, no LLM.

REAL: FastAPI app, RequestContextMiddleware, AgentOS auth layer, the ticket route,
WriteCommandTicketService, WriteCommandCoordinator, PostgresWriteCommandStore
(PostgreSQL), ExecutionCoordinator, GovernanceGate, CreateOperationalTicketHandler,
MockTicketingAdapter and MockTicketDesk.
Test doubles: the trusted ActorResolver, RecordingAuditSink and an
ExecutionCoordinator subclass that only counts entries.
"""

import asyncio
import json
from collections.abc import Awaitable, Callable
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest

from app.application.operations_tickets import WriteCommandTicketService
from app.context import ActorContext
from app.integrations.commerce.mock import MockTicketWriteMode
from app.main import create_app
from tests.integration.product_db import product_store, rows_for_key
from tests.integration.test_ticket_command_idempotency import (
    FailingCompleteStore,
    Instance,
    TicketSystem,
)
from tests.operations.helpers import OTHER_STORE, STORE, actor
from tests.support.actor_resolver import StaticActorResolver

pytestmark = pytest.mark.integration

PATH = "/api/v1/operations/tickets"
TITLE, DESCRIPTION = "Failed delivery", "Shipment requires operations follow-up."
WRITER = actor()  # tickets.create + exact grant of STORE


def new_key() -> str:
    return f"SENSITIVEHTTPKEY-{uuid4()}"


def body(**overrides) -> dict[str, Any]:
    return {"store_id": STORE, "title": TITLE, "description": DESCRIPTION} | overrides


class Node:
    """One application process: app + service + coordinator + its own Postgres store."""

    def __init__(self, app, instance: Instance) -> None:
        self.app, self.instance = app, instance

    async def post(self, payload, key: str | None, headers=None) -> httpx.Response:
        headers = dict(headers or {})
        if key is not None:
            headers["Idempotency-Key"] = key
        transport = httpx.ASGITransport(app=self.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.post(PATH, json=payload, headers=headers)


def run_node(
    settings,
    runtime_settings,
    migrated: str,
    system: TicketSystem,
    scenario: Callable[[Node], Awaitable[Any]],
    *,
    who: ActorContext | None = WRITER,
    wrap_store=None,
    configured: bool = True,
    **executor_options,
):
    async def main():
        async with product_store(migrated) as store:
            instance = Instance.build(wrap_store(store) if wrap_store else store, system,
                                      **executor_options)  # fmt: skip
            service = WriteCommandTicketService(instance.commands) if configured else None
            resolver = StaticActorResolver(who) if who is not None else None
            app = create_app(settings, runtime_settings, actor_resolver=resolver,
                             operations_ticket_service=service)  # fmt: skip
            return await scenario(Node(app, instance))

    return asyncio.run(main())


def check_verified(response: httpx.Response, *, replayed: bool) -> dict:
    data = response.json()
    assert response.status_code == (200 if replayed else 201)
    assert (data["status"], data["reason"], data["replayed"], data["persistence_complete"]) == (
        "verified", "verified", replayed, True,
    )  # fmt: skip
    assert data["request_id"] == response.headers["X-Request-ID"]
    UUID(data["ticket_id"])
    return data


def test_first_request_replay_and_restart_replay(
    settings, runtime_settings, migrated, engine
) -> None:
    key, system = new_key(), TicketSystem.build()

    async def first_and_replay(node: Node):
        first = await node.post(body(), key)
        events = list(node.instance.sink.events)
        replay = await node.post(body(), key)
        return first, replay, events, node

    first, replay, events, node_a = run_node(settings, runtime_settings, migrated, system,
                                             first_and_replay)  # fmt: skip
    data = check_verified(first, replayed=False)
    assert node_a.instance.executor.calls == 1
    assert system.desk.ticket_count == 1
    (call,) = system.spy.creates
    assert (call["title"], call["description"]) == (TITLE, DESCRIPTION)
    ticket = asyncio.run(system.spy.get_ticket(UUID(data["ticket_id"])))
    assert str(ticket.id) == data["ticket_id"] and str(ticket.store_id) == STORE
    assert events[-1].event_type.value == "verified" and events[-1].verification_code
    assert all(e.run_id == events[0].run_id for e in events)
    (row,) = rows_for_key(engine, key)
    assert row["status"] == "verified" and str(row["command_id"]) == data["command_id"]
    assert row["audit_complete"] is True

    again = check_verified(replay, replayed=True)
    assert (again["command_id"], again["ticket_id"]) == (data["command_id"], data["ticket_id"])
    assert again["request_id"] != data["request_id"]
    assert node_a.instance.executor.calls == 1
    assert node_a.instance.sink.events == events  # no second audit sequence

    # Node A is gone (engine disposed). A fresh app/service/store B replays durably.
    async def restart(node: Node):
        return await node.post(body(), key), node

    restarted, node_b = run_node(settings, runtime_settings, migrated, system, restart)
    later = check_verified(restarted, replayed=True)
    assert (later["command_id"], later["ticket_id"]) == (data["command_id"], data["ticket_id"])
    assert node_b.instance.executor.calls == 0 and node_b.instance.sink.events == []
    assert system.desk.ticket_count == 1 and len(system.spy.creates) == 1
    assert len(rows_for_key(engine, key)) == 1


def test_concurrent_http_duplicates_execute_at_most_once(
    settings, runtime_settings, migrated, engine
) -> None:
    key, system = new_key(), TicketSystem.build(slow=True)
    duplicates = 6

    async def one():
        async with product_store(migrated) as store:  # a separate process each
            instance = Instance.build(store, system)
            app = create_app(settings, runtime_settings,
                             actor_resolver=StaticActorResolver(WRITER),
                             operations_ticket_service=WriteCommandTicketService(
                                 instance.commands))  # fmt: skip
            return await Node(app, instance).post(body(), key), instance

    async def main():
        return await asyncio.gather(*(one() for _ in range(duplicates)))

    outcomes = asyncio.run(main())
    responses = [r for r, _ in outcomes]
    assert sum(i.executor.calls for _, i in outcomes) == 1
    datas = [r.json() for r in responses]
    assert len({d["command_id"] for d in datas}) == 1
    fresh = [(r, d) for r, d in zip(responses, datas, strict=True) if not d["replayed"]]
    assert len(fresh) == 1 and fresh[0][0].status_code == 201
    replays = [(r, d) for r, d in zip(responses, datas, strict=True) if d["replayed"]]
    assert len(replays) == duplicates - 1
    for response, data in replays:
        if data["status"] == "in_progress":
            assert response.status_code == 202 and data["ticket_id"] is None
            assert data["reason"] is None and data["persistence_complete"] is True
        else:
            assert (response.status_code, data["status"]) == (200, "verified")
    assert "in_progress" in {d["status"] for _, d in replays}  # overlapped the slow write
    assert system.desk.ticket_count == 1 and len(system.spy.creates) == 1
    (row,) = rows_for_key(engine, key)
    assert row["status"] == "verified"


@pytest.mark.parametrize(
    "changed",
    [body(title="Different title"), body(description="Different description"),
     body(store_id=OTHER_STORE)],
    ids=["title", "description", "granted-store"],
)  # fmt: skip
def test_same_key_for_a_different_request_is_409(
    settings, runtime_settings, migrated, engine, changed
) -> None:
    key, system = new_key(), TicketSystem.build()
    both_stores = actor(store_ids=frozenset({STORE, OTHER_STORE}))

    async def scenario(node: Node):
        return await node.post(body(), key), await node.post(changed, key), node

    first, conflict, node = run_node(settings, runtime_settings, migrated, system, scenario,
                                     who=both_stores)  # fmt: skip
    assert first.status_code == 201
    assert conflict.status_code == 409 and conflict.json() == {"detail": "Idempotency conflict"}
    for secret in (key, "Different", TITLE):
        assert secret not in conflict.text
    assert node.instance.executor.calls == 1
    assert system.desk.ticket_count == 1
    (row,) = rows_for_key(engine, key)
    assert row["store_id"] == STORE  # no command for the other store


def test_ungranted_changed_store_is_403_before_the_command_layer(
    settings, runtime_settings, migrated, engine
) -> None:
    key, system = new_key(), TicketSystem.build()

    async def scenario(node: Node):
        return await node.post(body(), key), await node.post(body(store_id=OTHER_STORE), key)

    first, forbidden = run_node(settings, runtime_settings, migrated, system, scenario)
    assert first.status_code == 201
    assert forbidden.status_code == 403 and forbidden.json() == {"detail": "Forbidden"}
    assert len(rows_for_key(engine, key)) == 1 and system.desk.ticket_count == 1


def test_denied_then_permission_change_then_new_key(
    settings, runtime_settings, migrated, engine
) -> None:
    key, system = new_key(), TicketSystem.build()
    no_permission = actor(permissions=frozenset())

    async def denied_scenario(node: Node):
        return await node.post(body(), key), node

    denied, node = run_node(settings, runtime_settings, migrated, system, denied_scenario,
                            who=no_permission)  # fmt: skip
    data = denied.json()
    assert denied.status_code == 200
    assert (data["status"], data["reason"], data["ticket_id"], data["replayed"]) == (
        "denied", "policy_denied", None, False,
    )  # fmt: skip
    assert node.instance.executor.calls == 1 and system.desk.ticket_count == 0
    (row,) = rows_for_key(engine, key)
    assert row["status"] == "denied"

    # The actor now holds tickets.create: the same key still replays DENIED.
    new = new_key()

    async def granted(node: Node):
        return await node.post(body(), key), await node.post(body(), new), node

    replay, fresh, node2 = run_node(settings, runtime_settings, migrated, system, granted)
    again = replay.json()
    assert replay.status_code == 200
    assert (again["status"], again["replayed"], again["command_id"], again["ticket_id"]) == (
        "denied", True, data["command_id"], None,
    )  # fmt: skip
    check_verified(fresh, replayed=False)  # a NEW key: governance evaluates again
    assert node2.instance.executor.calls == 1  # only the new key executed
    assert system.desk.ticket_count == 1


def test_uncertain_write_is_202_requires_human_without_ticket_id(
    settings, runtime_settings, migrated, engine
) -> None:
    key = new_key()
    system = TicketSystem.build(MockTicketWriteMode.UNCERTAIN_AFTER_WRITE)

    async def scenario(node: Node):
        return await node.post(body(), key), await node.post(body(), key), node

    first, again, node = run_node(settings, runtime_settings, migrated, system, scenario)
    data = first.json()
    assert first.status_code == 202
    assert (data["status"], data["reason"], data["ticket_id"], data["persistence_complete"]) == (
        "requires_human", "execution_outcome_uncertain", None, True,
    )  # fmt: skip
    assert system.desk.ticket_count == 1  # physically exists, exactly once
    replay = again.json()
    assert again.status_code == 202 and replay["replayed"] is True
    assert (replay["command_id"], replay["ticket_id"]) == (data["command_id"], None)
    assert node.instance.executor.calls == 1 and len(system.spy.creates) == 1


def test_terminal_persistence_failure_then_healthy_retry(
    settings, runtime_settings, migrated, engine
) -> None:
    key, system = new_key(), TicketSystem.build()

    async def failing(node: Node):
        return await node.post(body(), key), node

    first, node_a = run_node(settings, runtime_settings, migrated, system, failing,
                             wrap_store=FailingCompleteStore)  # fmt: skip
    data = first.json()
    assert first.status_code == 202
    assert (data["status"], data["reason"], data["ticket_id"], data["persistence_complete"],
            data["replayed"]) == ("requires_human", "command_persistence_incomplete", None,
                                  False, False)  # fmt: skip
    assert "SENSITIVE-DB-ERROR" not in first.text and "OperationalError" not in first.text
    assert system.desk.ticket_count == 1 and node_a.instance.executor.calls == 1
    (row,) = rows_for_key(engine, key)
    assert row["status"] == "in_progress"

    async def healthy(node: Node):
        return await node.post(body(), key), node

    retry, node_b = run_node(settings, runtime_settings, migrated, system, healthy)
    again = retry.json()
    assert retry.status_code == 202
    assert (again["status"], again["reason"], again["replayed"], again["persistence_complete"],
            again["ticket_id"]) == ("in_progress", None, True, True, None)  # fmt: skip
    assert again["command_id"] == data["command_id"]
    assert node_b.instance.executor.calls == 0 and system.desk.ticket_count == 1


def test_execution_exception_is_202_and_replays(
    settings, runtime_settings, migrated, engine
) -> None:
    key, system = new_key(), TicketSystem.build()

    async def broken(node: Node):
        return await node.post(body(), key)

    first = run_node(settings, runtime_settings, migrated, system, broken, raise_error=True)
    data = first.json()
    assert first.status_code == 202
    assert (data["status"], data["reason"], data["ticket_id"]) == (
        "requires_human", "command_execution_error", None,
    )  # fmt: skip
    assert "exploded" not in first.text and "SENSITIVE" not in first.text

    async def retry(node: Node):
        return await node.post(body(), key), node

    again, node = run_node(settings, runtime_settings, migrated, system, retry)
    assert again.status_code == 202 and again.json()["replayed"] is True
    assert node.instance.executor.calls == 0 and system.desk.ticket_count == 0


@pytest.mark.parametrize(
    ("payload", "key", "who", "code"),
    [
        (body(), None, WRITER, 400),  # missing key
        (body(), "", WRITER, 400),
        (body(), "has space", WRITER, 400),
        (body(), "k" * 129, WRITER, 400),
        (body(), "bad/char", WRITER, 400),
        (body(title="   "), "valid-key-1", WRITER, 422),
        (body(title="x" * 161), "valid-key-1", WRITER, 422),
        (body(description=""), "valid-key-1", WRITER, 422),
        (body(store_id="not-a-uuid"), "valid-key-1", WRITER, 422),
        (body(action_name="operations.other"), "valid-key-1", WRITER, 422),
        (body(idempotency_key="x"), "valid-key-1", WRITER, 422),
        (body(company_id="x", permissions=["tickets.create"]), "valid-key-1", WRITER, 422),
        (body(), "valid-key-1", None, 401),  # NoActorResolver
        (body(store_id=OTHER_STORE), "valid-key-1", WRITER, 403),
    ],
)
def test_rejections_create_no_command_and_no_ticket(
    settings, runtime_settings, migrated, engine, auth_headers, payload, key, who, code
) -> None:
    system = TicketSystem.build()
    company = f"company-{uuid4()}"
    # A per-test company keeps DB row checks isolated; the store UUID stays granted.
    who = who.model_copy(update={"company_id": company}) if who else None

    async def scenario(node: Node):
        return await node.post(payload, key, headers=auth_headers), node

    response, node = run_node(settings, runtime_settings, migrated, system, scenario, who=who)
    assert response.status_code == code
    assert node.instance.executor.calls == 0 and system.desk.ticket_count == 0
    from tests.integration.product_db import command_rows

    assert command_rows(engine, company) == []
    if key:
        assert key not in response.text


def test_unconfigured_service_is_503(settings, runtime_settings, migrated) -> None:
    system = TicketSystem.build()

    async def scenario(node: Node):
        return await node.post(body(), new_key())

    response = run_node(settings, runtime_settings, migrated, system, scenario, configured=False)
    assert response.status_code == 503
    assert response.json() == {"detail": "Operations ticket service unavailable"}
    assert system.desk.ticket_count == 0


def test_idempotency_key_and_provider_ids_never_leak(
    settings, runtime_settings, migrated, engine
) -> None:
    key, system = new_key(), TicketSystem.build()

    async def scenario(node: Node):
        return await node.post(body(), key), await node.post(body(), key), node

    first, replay, node = run_node(settings, runtime_settings, migrated, system, scenario)
    provider_keys = list(system.desk.list_ticket_keys())  # provider-side ticket keys
    assert provider_keys and all(k.startswith("tkt_") for k in provider_keys)
    for response in (first, replay):
        assert key not in response.text and "SENSITIVEHTTPKEY" not in response.text
        assert key not in json.dumps(dict(response.headers))
        for provider_key in provider_keys:
            assert provider_key not in response.text
        assert "action_run_id" not in response.json() and "audit" not in response.text
    (row,) = rows_for_key(engine, key)
    assert key not in json.dumps(row, default=str)
    events = json.dumps([e.model_dump(mode="json") for e in node.instance.sink.events])
    assert key not in events and "SENSITIVEHTTPKEY" not in events
