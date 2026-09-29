"""POST /api/v1/operations/tickets then GET /api/v1/operations/tickets/commands, end to end
against real PostgreSQL. No agent, no LLM.

REAL: FastAPI app, RequestContextMiddleware, AgentOS auth layer, both ticket routes,
WriteCommandTicketService, WriteCommandTicketQueryService, WriteCommandCoordinator,
PostgresWriteCommandStore (PostgreSQL), ExecutionCoordinator, GovernanceGate,
CreateOperationalTicketHandler, MockTicketingAdapter and MockTicketDesk.
Test doubles: the trusted ActorResolver, RecordingAuditSink, an ExecutionCoordinator
subclass that only counts entries, and a ticketing spy that counts every call.
"""

import asyncio
from datetime import datetime
from uuid import UUID, uuid4

import httpx
import pytest

from app.application.operations_ticket_queries import WriteCommandTicketQueryService
from app.application.operations_tickets import WriteCommandTicketService
from app.commands import CommandReason, CommandStatus, WriteCommandClaim, WriteCommandOutcome
from app.commands import hash_idempotency_key as key_hash
from app.context import ActorContext
from app.governance import ActionScope
from app.integrations.commerce.mock import (
    MockCommerceSystem,
    MockTicketDesk,
    MockTicketingAdapter,
    MockTicketWriteMode,
)
from app.main import create_app
from tests.integration.product_db import product_store, rows_for_key
from tests.integration.test_ticket_command_idempotency import (
    FailingCompleteStore,
    Instance,
    TicketSystem,
)
from tests.operations.helpers import (
    COMPANY,
    OTHER_COMPANY,
    OTHER_STORE,
    STORE,
    SpyTicketing,
    actor,
    request,
)
from tests.support.actor_resolver import StaticActorResolver

pytestmark = pytest.mark.integration

POST_PATH = "/api/v1/operations/tickets"
GET_PATH = "/api/v1/operations/tickets/commands"
TITLE, DESCRIPTION = "Failed delivery", "Shipment requires operations follow-up."
WRITER = actor()  # tickets.create + exact grant of STORE
NOT_FOUND = {"detail": "Ticket command not found"}


def new_key() -> str:
    return f"STATUSKEY-{uuid4()}"


def body(**overrides):
    return {"store_id": STORE, "title": TITLE, "description": DESCRIPTION} | overrides


class CountingTicketing(SpyTicketing):
    """Counts EVERY integration call, reads included."""

    def __init__(self, inner) -> None:
        super().__init__(inner)
        self.ticket_reads = 0

    async def get_ticket(self, ticket_id):
        self.ticket_reads += 1
        return await super().get_ticket(ticket_id)

    @property
    def total_calls(self) -> int:
        return len(self.creates) + len(self.correlation_reads) + self.ticket_reads


def ticket_system(mode=MockTicketWriteMode.NORMAL) -> TicketSystem:
    desk = MockTicketDesk(mode=mode)
    return TicketSystem(desk, CountingTicketing(MockTicketingAdapter(MockCommerceSystem(), desk)))


class Env:
    """One application: POST and GET services sharing one Postgres store."""

    def __init__(self, settings, runtime_settings, store, instance: Instance) -> None:
        self.settings, self.runtime_settings = settings, runtime_settings
        self.store, self.instance = store, instance
        self.write = WriteCommandTicketService(instance.commands)
        self.query = WriteCommandTicketQueryService(store)

    def app(self, who: ActorContext | None):
        return create_app(
            self.settings, self.runtime_settings,
            actor_resolver=StaticActorResolver(who) if who is not None else None,
            operations_ticket_service=self.write,
            operations_ticket_query_service=self.query,
        )  # fmt: skip

    async def post(self, who, payload, key, headers=None) -> httpx.Response:
        return await self._call(who, "POST", POST_PATH, json=payload,
                                headers={"Idempotency-Key": key, **(headers or {})})  # fmt: skip

    async def get(self, who, command_id, headers=None) -> httpx.Response:
        return await self._call(who, "GET", GET_PATH, params={"command_id": str(command_id)},
                                headers=headers or {})  # fmt: skip

    async def _call(self, who, method, path, **kwargs) -> httpx.Response:
        transport = httpx.ASGITransport(app=self.app(who))
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.request(method, path, **kwargs)


def run(settings, runtime_settings, migrated, system, scenario, *, write_store_wrapper=None):
    async def main():
        async with product_store(migrated) as store:
            write_store = write_store_wrapper(store) if write_store_wrapper else store
            env = Env(settings, runtime_settings, store, Instance.build(write_store, system))
            return await scenario(env)

    return asyncio.run(main())


def side_effects(env: Env, system: TicketSystem, engine, key) -> tuple:
    return (
        env.instance.executor.calls,
        len(env.instance.sink.events),
        system.spy.total_calls,  # type: ignore[attr-defined]
        system.desk.ticket_count,
        rows_for_key(engine, key),
    )


def test_verified_post_then_get(settings, runtime_settings, migrated, engine) -> None:
    key, system = new_key(), ticket_system()

    async def scenario(env: Env):
        post = await env.post(WRITER, body(), key)
        before = side_effects(env, system, engine, key)
        gets = [
            await env.get(
                WRITER,
                post.json()["command_id"],
                headers={"Idempotency-Key": "unrelated-key"} if i else None,
            )  # fmt: skip
            for i in range(3)
        ]
        return post, gets, before, side_effects(env, system, engine, key)

    post, gets, before, after = run(settings, runtime_settings, migrated, system, scenario)
    assert post.status_code == 201
    posted = post.json()
    (row,) = before[4]
    for response in gets:
        assert response.status_code == 200
        data = response.json()
        assert data == {
            "request_id": response.headers["X-Request-ID"],
            "command_id": posted["command_id"], "status": "verified", "reason": "verified",
            "ticket_id": posted["ticket_id"],
            "created_at": data["created_at"], "updated_at": data["updated_at"],
        }  # fmt: skip
        assert data["request_id"] != posted["request_id"]
        created, updated = (datetime.fromisoformat(data[k]) for k in ("created_at", "updated_at"))
        assert created.tzinfo is not None and updated.tzinfo is not None
        assert (created, updated) == (row["created_at"], row["updated_at"])
        assert "unrelated-key" not in response.text
    assert len({r.json()["request_id"] for r in gets}) == 3
    # Pure read: no execution, audit, integration call, ticket or row change at all.
    assert after == before


def test_persistence_failure_post_then_get_reports_durable_in_progress(
    settings, runtime_settings, migrated, engine
) -> None:
    key, system = new_key(), ticket_system()

    async def scenario(env: Env):
        post = await env.post(WRITER, body(), key)
        before = side_effects(env, system, engine, key)
        get = await env.get(WRITER, post.json()["command_id"])
        return post, get, before, side_effects(env, system, engine, key)

    post, get, before, after = run(settings, runtime_settings, migrated, system, scenario,
                                   write_store_wrapper=FailingCompleteStore)  # fmt: skip
    posted = post.json()
    assert post.status_code == 202
    assert (posted["status"], posted["reason"], posted["persistence_complete"]) == (
        "requires_human", "command_persistence_incomplete", False,
    )  # fmt: skip
    (row,) = before[4]
    assert row["status"] == "in_progress"
    # GET reports the persisted truth, not the transient POST outcome.
    data = get.json()
    assert get.status_code == 200
    assert (data["command_id"], data["status"], data["reason"], data["ticket_id"]) == (
        posted["command_id"], "in_progress", None, None,
    )  # fmt: skip
    assert "command_persistence_incomplete" not in get.text
    assert "persistence_complete" not in data and "replayed" not in data
    assert system.desk.ticket_count == 1  # the effect exists; nothing retried it
    assert after == before


def test_uncertain_write_reads_requires_human_without_ticket(settings, runtime_settings,
                                                            migrated, engine) -> None:  # fmt: skip
    key, system = new_key(), ticket_system(MockTicketWriteMode.UNCERTAIN_AFTER_WRITE)

    async def scenario(env: Env):
        post = await env.post(WRITER, body(), key)
        return post, await env.get(WRITER, post.json()["command_id"])

    post, get = run(settings, runtime_settings, migrated, system, scenario)
    assert post.status_code == 202
    data = get.json()
    assert get.status_code == 200
    assert (data["status"], data["reason"], data["ticket_id"]) == (
        "requires_human", "execution_outcome_uncertain", None,
    )  # fmt: skip
    assert system.desk.ticket_count == 1  # physically there, never exposed as confirmed
    for ticket_key in system.desk.list_ticket_keys():
        assert ticket_key not in get.text


def test_denied_command_reads_denied_without_regoverning(settings, runtime_settings, migrated,
                                                        engine) -> None:  # fmt: skip
    key, system = new_key(), ticket_system()
    no_permission = actor(permissions=frozenset())

    async def scenario(env: Env):
        post = await env.post(no_permission, body(), key)
        calls = env.instance.executor.calls
        get = await env.get(no_permission, post.json()["command_id"])
        return post, get, calls, env.instance.executor.calls

    post, get, calls_before, calls_after = run(settings, runtime_settings, migrated, system,
                                               scenario)  # fmt: skip
    assert post.status_code == 200 and post.json()["status"] == "denied"
    data = get.json()
    assert get.status_code == 200
    assert (data["status"], data["reason"], data["ticket_id"]) == ("denied", "policy_denied", None)
    assert calls_before == calls_after == 1


def test_failed_command_reads_failed(settings, runtime_settings, migrated, engine) -> None:
    # The strict HTTP body rejects a blank title, so submit through the service directly.
    key, system = new_key(), ticket_system()

    async def scenario(env: Env):
        failed = await env.write.create_ticket(
            request(WRITER), ActionScope(company_id=COMPANY, store_id=STORE), "   ",
            DESCRIPTION, key,
        )  # fmt: skip
        calls = env.instance.executor.calls
        return failed, await env.get(WRITER, failed.command_id), calls, env.instance.executor.calls

    failed, get, before, after = run(settings, runtime_settings, migrated, system, scenario)
    assert (failed.status.value, failed.reason.value) == ("failed", "input_invalid")
    data = get.json()
    assert get.status_code == 200
    assert (data["status"], data["reason"], data["ticket_id"]) == ("failed", "input_invalid", None)
    assert before == after == 1 and system.desk.ticket_count == 0


def test_ownership_scope_and_idor(settings, runtime_settings, migrated, engine) -> None:
    key, system = new_key(), ticket_system()
    everywhere = frozenset({STORE, OTHER_STORE})
    viewers = {
        "other-actor-same-company": actor(actor_id="ops-user-2", store_ids=everywhere),
        "same-actor-id-other-company": actor(company_id=OTHER_COMPANY, store_ids=everywhere),
        "other-actor-other-company": actor(
            actor_id="ops-user-9", company_id=OTHER_COMPANY, store_ids=everywhere
        ),  # fmt: skip
        "revoked-store": WRITER.model_copy(update={"store_ids": frozenset()}),
        "other-store-only": WRITER.model_copy(update={"store_ids": frozenset({OTHER_STORE})}),
        "wildcard-star": WRITER.model_copy(update={"store_ids": frozenset({"*"})}),
        "wildcard-all": WRITER.model_copy(update={"store_ids": frozenset({"all"})}),
        "wildcard-stores": WRITER.model_copy(update={"store_ids": frozenset({"stores.*"})}),
    }

    async def scenario(env: Env):
        post = await env.post(WRITER, body(), key)
        command_id = post.json()["command_id"]
        before = side_effects(env, system, engine, key)
        results = {name: await env.get(who, command_id) for name, who in viewers.items()}
        results["unknown-command"] = await env.get(WRITER, uuid4())
        results["unauthenticated"] = await env.get(None, command_id)
        permission_revoked = await env.get(
            WRITER.model_copy(update={"permissions": frozenset()}), command_id
        )
        return post, results, permission_revoked, before, side_effects(env, system, engine, key)

    post, results, permission_revoked, before, after = run(
        settings, runtime_settings, migrated, system, scenario
    )
    posted = post.json()
    unauthenticated = results.pop("unauthenticated")
    assert unauthenticated.status_code == 401
    for name, response in results.items():
        assert (response.status_code, response.json()) == (404, NOT_FOUND), name
        for secret in (posted["ticket_id"], posted["command_id"], STORE, COMPANY, "verified"):
            assert secret not in response.text, name
    # Write permission is not needed to read one's own command in a granted store.
    data = permission_revoked.json()
    assert permission_revoked.status_code == 200
    assert (data["status"], data["ticket_id"]) == ("verified", posted["ticket_id"])
    assert after == before  # none of the reads did anything


def test_another_action_of_the_same_principal_is_not_found(settings, runtime_settings,
                                                          migrated, engine) -> None:  # fmt: skip
    system = ticket_system()
    other_key = new_key()

    async def scenario(env: Env):
        claimed = await env.store.claim(WriteCommandClaim(
            command_id=uuid4(), company_id=COMPANY, actor_id=WRITER.actor_id, store_id=STORE,
            action_name="operations.test_note.add", idempotency_key_hash=key_hash(other_key),
            request_fingerprint="a" * 64,
        ))  # fmt: skip
        await env.store.complete(claimed.record.command_id, WriteCommandOutcome(
            status=CommandStatus.VERIFIED, reason=CommandReason.VERIFIED,
            action_run_id=uuid4(), execution_reference_id="note-ref-123", audit_complete=True,
        ))  # fmt: skip
        return await env.get(WRITER, claimed.record.command_id)

    response = run(settings, runtime_settings, migrated, system, scenario)
    assert (response.status_code, response.json()) == (404, NOT_FOUND)
    for secret in ("test_note", "note-ref-123", "verified"):
        assert secret not in response.text


def test_post_semantics_are_unchanged(settings, runtime_settings, migrated, engine) -> None:
    key, system = new_key(), ticket_system()

    async def scenario(env: Env):
        first = await env.post(WRITER, body(), key)
        replay = await env.post(WRITER, body(), key)
        conflict = await env.post(WRITER, body(title="Other"), key)
        return first, replay, conflict

    first, replay, conflict = run(settings, runtime_settings, migrated, system, scenario)
    assert (first.status_code, replay.status_code, conflict.status_code) == (201, 200, 409)
    assert first.json()["command_id"] == replay.json()["command_id"]
    assert replay.json()["replayed"] is True and system.desk.ticket_count == 1
    assert UUID(first.json()["ticket_id"])
