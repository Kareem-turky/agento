"""Task 037 on migrated PostgreSQL: the REAL PostgresConversationRepository and the EXISTING
PostgresIntegrationConnectionRepository behind the real ConversationIngress, delivery
recorder and read service (TEST-ONLY messaging definition; no real provider).

Atomic ingestion, one conversation and one message under concurrent identical retries,
unique monotonic sequences under concurrent distinct messages, conflicting duplicates,
company and connection isolation, history surviving connection removal, delivery
idempotency / staleness and the append-only trigger, CHECK constraints, store scope in
SQL, the real deployment app's empty state, and inert untrusted text that triggers no
audit, approval or Workflow. No model, no network beyond the local database."""

import asyncio
import socket
from datetime import timedelta
from uuid import uuid4

import pytest
import sqlalchemy as sa

from app.conversations.delivery import DeliveryOutcome, DeliveryUpdate
from app.conversations.errors import (
    DeliveryEventConflictError,
    InboundMessageConflictError,
    InboundMessageRefusedError,
    IngressRefusal,
)
from app.conversations.ingress import ConversationDelivery, ConversationIngress
from app.conversations.models import AuthorKind, DeliveryState
from app.conversations.permissions import CONVERSATION_ACTIONS
from app.conversations.service import ConversationReadService
from app.governance import ActionCatalog, GovernanceGate
from app.persistence import (
    PostgresConversationRepository,
    PostgresIntegrationConnectionRepository,
    create_product_engine,
)
from app.persistence.database import create_session_factory
from tests.support.conversation_fakes import (
    INJECTION,
    SCRIPT,
    STORE_A,
    STORE_B,
    T0,
    chat_catalog,
    connection,
    envelope,
    reader,
)

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    real = socket.socket.connect

    def local_only(sock: socket.socket, address: object) -> object:
        host = address[0] if isinstance(address, tuple) else address
        if host not in ("127.0.0.1", "::1", "localhost") and not str(host).startswith("/"):
            raise AssertionError("unexpected outbound connection")
        return real(sock, address)

    monkeypatch.setattr(socket.socket, "connect", local_only)


class PG:
    def __init__(self, database_url: str) -> None:
        self.engine = create_product_engine(database_url)
        sessions = create_session_factory(self.engine)
        self.company = str(uuid4())
        self.repository = PostgresConversationRepository(sessions)
        self.connections = PostgresIntegrationConnectionRepository(sessions)
        catalog = chat_catalog()
        self.ingress = ConversationIngress(self.repository, self.connections, catalog)
        self.delivery = ConversationDelivery(self.repository)
        self.service = ConversationReadService(
            self.repository, GovernanceGate(ActionCatalog(CONVERSATION_ACTIONS)),
            connections=self.connections, catalog=catalog)  # fmt: skip

    async def channel(self, company: str | None = None):
        created = connection(company or self.company)
        await self.connections.insert(created)
        return created

    def context(self, conn, store=None):
        from app.conversations.models import ChannelContext

        return ChannelContext(company_id=conn.company_id, connection_id=conn.connection_id,
                              store_id=store)  # fmt: skip


def scenario(database_url: str, body):
    async def main():
        pg = PG(database_url)
        try:
            return await body(pg)
        finally:
            await pg.engine.dispose()

    return asyncio.run(main())


def rows(engine: sa.Engine, sql: str, **params) -> list[dict]:
    with engine.connect() as connection_:
        return [dict(r) for r in connection_.execute(sa.text(sql), params).mappings()]


def test_concurrent_identical_first_messages_are_one_message(migrated, engine) -> None:
    async def body(pg: PG):
        conn = await pg.channel()
        results = await asyncio.gather(*(pg.ingress.ingest(pg.context(conn), envelope())
                                         for _ in range(10)))  # fmt: skip
        return pg.company, results

    company, results = scenario(migrated, body)
    assert len({r.message.message_id for r in results}) == 1
    assert len({r.conversation.conversation_id for r in results}) == 1
    assert [r.replayed for r in results].count(False) == 1
    assert rows(engine, "SELECT count(*) AS n FROM product.conversations WHERE company_id = :c",
                c=company) == [{"n": 1}]  # fmt: skip
    (message,) = rows(
        engine,
        "SELECT sequence, text, delivery_state FROM "
        "product.conversation_messages WHERE company_id = :c",
        c=company,
    )
    assert message == {"sequence": 1, "text": "Where is my order?", "delivery_state": "received"}


def test_concurrent_distinct_messages_get_unique_increasing_sequences(migrated, engine) -> None:
    async def body(pg: PG):
        conn = await pg.channel()
        await pg.ingress.ingest(pg.context(conn), envelope())
        results = await asyncio.gather(*(
            pg.ingress.ingest(pg.context(conn), envelope(f"m{i}", ref=f"r-{i}"))
            for i in range(12)))  # fmt: skip
        return pg.company, results

    company, results = scenario(migrated, body)
    sequences = [r["sequence"] for r in rows(
        engine, "SELECT sequence FROM product.conversation_messages WHERE company_id = :c "
        "ORDER BY sequence", c=company)]  # fmt: skip
    assert sequences == list(range(1, 14))
    (conversation,) = rows(
        engine,
        "SELECT next_message_sequence, last_message_id FROM "
        "product.conversations WHERE company_id = :c",
        c=company,
    )
    assert conversation["next_message_sequence"] == 14
    last = max(results, key=lambda r: r.message.sequence)
    assert conversation["last_message_id"] == last.message.message_id


def test_conflicting_duplicate_keeps_the_original(migrated, engine) -> None:
    async def body(pg: PG):
        conn = await pg.channel()
        first = await pg.ingress.ingest(pg.context(conn), envelope("A"))
        replay = await pg.ingress.ingest(pg.context(conn), envelope("A"))
        with pytest.raises(InboundMessageConflictError):
            await pg.ingress.ingest(pg.context(conn), envelope("B"))
        with pytest.raises(InboundMessageConflictError):  # same message ref, other thread
            await pg.ingress.ingest(pg.context(conn), envelope("A", thread="t-2"))
        return pg.company, first, replay

    company, first, replay = scenario(migrated, body)
    assert replay.replayed and replay.message == first.message
    assert rows(engine, "SELECT text, sequence FROM product.conversation_messages WHERE "
                "company_id = :c", c=company) == [{"text": "A", "sequence": 1}]  # fmt: skip
    assert rows(engine, "SELECT count(*) AS n FROM product.conversations WHERE company_id = :c",
                c=company) == [{"n": 1}]  # fmt: skip


def test_company_and_connection_isolation_with_the_same_external_refs(migrated) -> None:
    async def body(pg: PG):
        mine, second = await pg.channel(), await pg.channel()
        other_company = str(uuid4())
        theirs = await pg.channel(other_company)
        results = [await pg.ingress.ingest(pg.context(c), envelope())
                   for c in (mine, second, theirs)]  # fmt: skip
        visible = await pg.service.list_conversations(reader(company=pg.company))
        foreign_view = await pg.service.list_conversations(reader(company=other_company))
        return results, visible, foreign_view

    results, visible, foreign_view = scenario(migrated, body)
    assert len({r.conversation.conversation_id for r in results}) == 3
    assert all(not r.replayed for r in results)
    assert {s.conversation.conversation_id for s in visible} == {
        results[0].conversation.conversation_id,
        results[1].conversation.conversation_id,
    }
    assert [s.conversation.conversation_id for s in foreign_view] == [
        results[2].conversation.conversation_id
    ]


def test_history_survives_connection_removal_but_ingress_stops(migrated, engine) -> None:
    async def body(pg: PG):
        conn = await pg.channel()
        first = await pg.ingress.ingest(pg.context(conn), envelope())
        assert await pg.connections.delete(pg.company, conn.connection_id)
        with pytest.raises(InboundMessageRefusedError) as info:
            await pg.ingress.ingest(pg.context(conn), envelope("n", ref="m-2"))
        summary = await pg.service.get_conversation(
            reader(company=pg.company), first.conversation.conversation_id
        )
        page = await pg.service.list_messages(
            reader(company=pg.company), first.conversation.conversation_id
        )
        return info.value.reason, summary, page

    reason, summary, page = scenario(migrated, body)
    assert reason is IngressRefusal.CONNECTION_NOT_FOUND
    assert summary.channel.connection_name is None and len(page.messages) == 1


def test_disabled_connection_ingests_nothing(migrated) -> None:
    async def body(pg: PG):
        conn = connection(pg.company, enabled=False)
        await pg.connections.insert(conn)
        with pytest.raises(InboundMessageRefusedError) as info:
            await pg.ingress.ingest(pg.context(conn), envelope())
        return info.value.reason, await pg.repository.list_conversations(
            pg.company, store_ids=frozenset(), connection_id=None, limit=10
        )

    reason, listed = scenario(migrated, body)
    assert reason is IngressRefusal.CONNECTION_DISABLED and listed == ()


def test_delivery_events_are_idempotent_never_regress_and_append_only(migrated, engine) -> None:
    async def body(pg: PG):
        conn = await pg.channel()
        first = await pg.ingress.ingest(pg.context(conn), envelope())
        message = await pg.repository.append_outbound(
            pg.company,
            first.conversation.conversation_id,
            message_id=uuid4(),
            text="Reply",
            author_kind=AuthorKind.HUMAN,
            actor_id="operator-1",
            actor_type="user",
            now=T0,
        )
        mid = message.message_id

        def u(state, ref, seconds=0):
            return DeliveryUpdate(state=state, occurred_at=T0 + timedelta(seconds=seconds),
                                  external_event_ref=ref)  # fmt: skip

        outcomes = [await pg.delivery.record(pg.company, mid, u("accepted", "e1")),
                    await pg.delivery.record(pg.company, mid, u("accepted", "e1")),
                    await pg.delivery.record(pg.company, mid, u("delivered", "e2", 2)),
                    await pg.delivery.record(pg.company, mid, u("sent", "e3", 1))]  # fmt: skip
        with pytest.raises(DeliveryEventConflictError):
            await pg.delivery.record(pg.company, mid, u("failed", "e1"))
        racing = await asyncio.gather(*(pg.delivery.record(pg.company, mid, u("failed", f"z{i}"))
                                        for i in range(5)))  # fmt: skip
        return message, outcomes, racing

    message, outcomes, racing = scenario(migrated, body)
    assert [o.outcome for o in outcomes] == [
        DeliveryOutcome.APPLIED,
        DeliveryOutcome.DUPLICATE,
        DeliveryOutcome.APPLIED,
        DeliveryOutcome.STALE,
    ]
    assert {o.state for o in racing} == {DeliveryState.DELIVERED}  # terminal: all stale
    assert {o.outcome for o in racing} == {DeliveryOutcome.STALE}
    events = rows(engine, "SELECT sequence, state, external_event_ref, applied FROM "
                  "product.message_delivery_events WHERE message_id = :m ORDER BY sequence",
                  m=message.message_id)  # fmt: skip
    # Applied transitions and every REFERENCED stale observation (applied = false), in
    # Product append order; the message state stays delivered.
    assert events[:3] == [
        {"sequence": 1, "state": "accepted", "external_event_ref": "e1", "applied": True},
        {"sequence": 2, "state": "delivered", "external_event_ref": "e2", "applied": True},
        {"sequence": 3, "state": "sent", "external_event_ref": "e3", "applied": False},
    ]
    assert [e["sequence"] for e in events] == list(range(1, 9))
    assert {(e["state"], e["applied"]) for e in events[3:]} == {("failed", False)}
    assert rows(engine, "SELECT delivery_state FROM product.conversation_messages "
                "WHERE message_id = :m", m=message.message_id) == [
        {"delivery_state": "delivered"}]  # fmt: skip
    for statement in (
        "UPDATE product.message_delivery_events SET state = 'failed' WHERE message_id = :m",
        "DELETE FROM product.message_delivery_events WHERE message_id = :m",
    ):
        with pytest.raises(sa.exc.DBAPIError), engine.begin() as connection_:
            connection_.execute(sa.text(statement), {"m": message.message_id})


def test_sql_constraints_hold(migrated, engine) -> None:
    async def body(pg: PG):
        conn = await pg.channel()
        first = await pg.ingress.ingest(pg.context(conn), envelope(ref="x" * 256))
        return first

    first = scenario(migrated, body)
    mid, cid = first.message.message_id, first.conversation.conversation_id
    for statement in (
        "UPDATE product.conversation_messages SET delivery_state = 'pending' WHERE message_id = :m",
        "UPDATE product.conversation_messages SET author_kind = 'agent' WHERE message_id = :m",
        "UPDATE product.conversation_messages SET text = repeat('x', 16001) WHERE message_id = :m",
        "UPDATE product.conversation_messages SET text = '' WHERE message_id = :m",
        "UPDATE product.conversation_messages SET external_message_ref = 'has space' "
        "WHERE message_id = :m",
        "UPDATE product.conversation_messages SET external_message_ref = repeat('y', 257) "
        "WHERE message_id = :m",
        "UPDATE product.conversation_messages SET direction = 'sideways' WHERE message_id = :m",
        "UPDATE product.conversation_messages SET sequence = 0 WHERE message_id = :m",
    ):
        with pytest.raises(sa.exc.DBAPIError), engine.begin() as connection_:
            connection_.execute(sa.text(statement), {"m": mid})
    with pytest.raises(sa.exc.IntegrityError), engine.begin() as connection_:  # duplicate seq
        connection_.execute(sa.text(
            "INSERT INTO product.conversation_messages SELECT gen_random_uuid(), "
            "conversation_id, company_id, connection_id, sequence, direction, author_kind, "
            "'other-ref', NULL, text, content_fingerprint, occurred_at, recorded_at, "
            "delivery_state, NULL, NULL FROM product.conversation_messages "
            "WHERE message_id = :m"), {"m": mid})  # fmt: skip
    assert rows(engine, "SELECT count(*) AS n FROM product.conversation_messages WHERE "
                "conversation_id = :c", c=cid) == [{"n": 1}]  # fmt: skip


def test_store_scope_is_applied_in_sql(migrated) -> None:
    async def body(pg: PG):
        conn = await pg.channel()
        await pg.ingress.ingest(pg.context(conn), envelope(thread="t-none"))
        await pg.ingress.ingest(pg.context(conn, STORE_A), envelope(thread="t-a", ref="a"))
        await pg.ingress.ingest(pg.context(conn, STORE_B), envelope(thread="t-b", ref="b"))
        with pytest.raises(InboundMessageRefusedError):
            await pg.ingress.ingest(pg.context(conn, STORE_B), envelope(thread="t-a", ref="a2"))
        only_a = await pg.service.list_conversations(reader(company=pg.company))
        none = await pg.repository.list_conversations(pg.company, store_ids=frozenset(),
                                                      connection_id=None, limit=10)  # fmt: skip
        return only_a, none

    only_a, none = scenario(migrated, body)
    assert sorted(s.conversation.store_id or "-" for s in only_a) == ["-", STORE_A]
    assert [c.store_id for c in none] == [None]


def test_untrusted_text_is_inert_and_audits_nothing(migrated, engine) -> None:
    async def body(pg: PG):
        conn = await pg.channel()
        results = [await pg.ingress.ingest(pg.context(conn), envelope(t, ref=f"i-{i}"))
                   for i, t in enumerate((INJECTION, SCRIPT))]  # fmt: skip
        page = await pg.service.list_messages(
            reader(company=pg.company), results[0].conversation.conversation_id
        )
        return pg.company, page

    company, page = scenario(migrated, body)
    assert [m.text for m in page.messages] == [INJECTION, SCRIPT]
    for table in ("audit_events", "approval_requests", "workflow_runs", "write_commands",
                  "knowledge_documents"):  # fmt: skip
        assert rows(engine, f"SELECT count(*) AS n FROM product.{table} "  # noqa: S608
                    "WHERE company_id = :c", c=company) == [{"n": 0}], table  # fmt: skip


def test_deployment_app_serves_an_empty_conversation_list(settings, runtime_settings,
                                                         migrated) -> None:  # fmt: skip
    from fastapi.testclient import TestClient

    from app.bootstrap import create_deployment_app
    from tests.support.product_auth import TEST_PRODUCT_KEY, deployment_settings, principal

    company = str(uuid4())
    keys = (principal(TEST_PRODUCT_KEY, permissions=frozenset({"conversations.read"})),)
    configured = deployment_settings(settings, "test", business_backend="disabled",
                                     product_api_keys=keys, company_id=company)  # fmt: skip
    app = create_deployment_app(configured, runtime_settings)
    headers = {"Authorization": f"Bearer {TEST_PRODUCT_KEY}"}
    with TestClient(app) as client:
        listed = client.get("/api/v1/conversations", headers=headers)
        assert listed.status_code == 200 and listed.json()["conversations"] == []
        missing = client.get("/api/v1/conversations/messages", headers=headers,
                             params={"conversation_id": str(uuid4())})  # fmt: skip
        assert missing.status_code == 404
        assert client.get("/api/v1/conversations").status_code == 401


async def _delivered(pg: PG):
    conn = await pg.channel()
    first = await pg.ingress.ingest(pg.context(conn), envelope())
    message = await pg.repository.append_outbound(
        pg.company,
        first.conversation.conversation_id,
        message_id=uuid4(),
        text="Reply",
        author_kind=AuthorKind.HUMAN,
        actor_id="operator-1",
        actor_type="user",
        now=T0,
    )
    for i, (state, ref) in enumerate((("accepted", "e1"), ("delivered", "e2"))):
        await pg.delivery.record(
            pg.company,
            message.message_id,
            DeliveryUpdate(
                state=state, occurred_at=T0 + timedelta(seconds=i), external_event_ref=ref
            ),
        )
    return message.message_id


def _stale(state: str = "sent", seconds: int = 1) -> DeliveryUpdate:
    return DeliveryUpdate(state=state, occurred_at=T0 + timedelta(seconds=seconds),
                          external_event_ref="e-stale")  # fmt: skip


def _observations(engine: sa.Engine, message_id) -> list[dict]:
    return rows(
        engine,
        "SELECT state, applied FROM product.message_delivery_events WHERE "
        "message_id = :m AND external_event_ref = 'e-stale'",
        m=message_id,
    )


def _state(engine: sa.Engine, message_id) -> str:
    (row,) = rows(
        engine,
        "SELECT delivery_state FROM product.conversation_messages WHERE message_id = :m",
        m=message_id,
    )
    return row["delivery_state"]


def test_a_referenced_stale_event_is_durably_idempotent(migrated, engine) -> None:
    async def body(pg: PG):
        mid = await _delivered(pg)
        first = await pg.delivery.record(pg.company, mid, _stale())
        replay = await pg.delivery.record(pg.company, mid, _stale())
        conflicts = 0
        for conflicting in (_stale("failed", 1), _stale("sent", 9)):
            try:
                await pg.delivery.record(pg.company, mid, conflicting)
            except DeliveryEventConflictError:
                conflicts += 1
        return mid, first, replay, conflicts

    mid, first, replay, conflicts = scenario(migrated, body)
    assert (first.outcome, first.state) == (DeliveryOutcome.STALE, DeliveryState.DELIVERED)
    assert (replay.outcome, replay.state) == (DeliveryOutcome.DUPLICATE,
                                              DeliveryState.DELIVERED)  # fmt: skip
    assert conflicts == 2
    assert _observations(engine, mid) == [{"state": "sent", "applied": False}]
    assert _state(engine, mid) == "delivered"  # never regresses
    # SQL: an unapplied observation always carries a provider event ref.
    with pytest.raises(sa.exc.IntegrityError), engine.begin() as connection_:
        connection_.execute(sa.text(
            "INSERT INTO product.message_delivery_events (message_id, company_id, sequence, "
            "state, occurred_at, recorded_at, external_event_ref, applied) SELECT message_id, "
            "company_id, 99, 'sent', now(), now(), NULL, false FROM "
            "product.conversation_messages WHERE message_id = :m"), {"m": mid})  # fmt: skip


def test_concurrent_copies_of_a_stale_event_store_one_observation(migrated, engine) -> None:
    async def body(pg: PG):
        mid = await _delivered(pg)
        results = await asyncio.gather(*(pg.delivery.record(pg.company, mid, _stale())
                                         for _ in range(8)))  # fmt: skip
        return mid, results

    mid, results = scenario(migrated, body)
    outcomes = sorted(r.outcome.value for r in results)
    assert outcomes == ["duplicate"] * 7 + ["stale"]
    assert {r.state for r in results} == {DeliveryState.DELIVERED}
    assert _observations(engine, mid) == [{"state": "sent", "applied": False}]
    assert _state(engine, mid) == "delivered"


def test_concurrent_conflicting_copies_store_one_and_fail_the_other(migrated, engine) -> None:
    async def body(pg: PG):
        mid = await _delivered(pg)
        racers = [_stale("sent", 1), _stale("failed", 1), _stale("sent", 5), _stale("unknown", 2)]
        results = await asyncio.gather(*(pg.delivery.record(pg.company, mid, u)
                                         for u in racers), return_exceptions=True)  # fmt: skip
        return mid, results

    mid, results = scenario(migrated, body)
    stored = [r for r in results if not isinstance(r, Exception)]
    assert len(stored) == 1 and stored[0].outcome is DeliveryOutcome.STALE
    assert all(isinstance(r, DeliveryEventConflictError) for r in results if r not in stored)
    assert len(_observations(engine, mid)) == 1
    assert _state(engine, mid) == "delivered"
