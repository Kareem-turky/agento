"""ConversationIngress (Task 037): trusted channel resolution through the EXISTING
Integration Foundation, atomic ingestion, idempotent replay, conflicting duplicates,
Product sequence, source vs Product time, connection removal, fail-closed storage and
inert untrusted text. In-memory repositories (PostgreSQL in tests/integration)."""

import asyncio
import socket
from datetime import timedelta
from uuid import uuid4

import pytest

from app.conversations.errors import (
    ConversationUnavailableError,
    InboundMessageConflictError,
    InboundMessageRefusedError,
    IngressRefusal,
)
from app.conversations.models import ChannelContext
from app.observability import ObservationOutcome, ProductOperation
from tests.support.conversation_fakes import (
    COMMERCE,
    COMPANY,
    INJECTION,
    MESSAGING,
    OTHER_COMPANY,
    SCRIPT,
    STORE_A,
    STORE_B,
    T0,
    ConversationWorld,
    envelope,
)
from tests.support.observability import RecordingObservability

R = IngressRefusal


def run(coroutine):
    return asyncio.run(coroutine)


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(_socket: object, address: object) -> None:
        raise AssertionError("unexpected outbound connection")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", refuse)


def test_first_message_creates_the_conversation_atomically() -> None:
    world = ConversationWorld()
    conn = run(world.channel())
    result = run(world.ingress.ingest(world.context(conn), envelope()))
    assert not result.replayed
    c, m = result.conversation, result.message
    assert (c.company_id, c.connection_id, c.integration_id) == (COMPANY, conn.connection_id,
                                                                 "example-chat")  # fmt: skip
    assert c.external_conversation_ref == "thread-1" and c.store_id is None
    assert (c.last_message_id, c.last_message_at) == (m.message_id, m.recorded_at)
    assert (m.sequence, m.direction.value, m.author_kind.value, m.delivery_state.value) == (
        1,
        "inbound",
        "external",
        "received",
    )
    assert m.occurred_at == T0 and m.recorded_at > T0  # source time kept; Product clock
    assert m.created_by_actor_id is None


def test_messages_get_increasing_product_sequences_independent_of_source_time() -> None:
    world = ConversationWorld()
    conn = run(world.channel())
    late = envelope("late", ref="m-2", at=T0 - timedelta(hours=1))  # older source time
    first = run(world.ingress.ingest(world.context(conn), envelope()))
    second = run(world.ingress.ingest(world.context(conn), late))
    third = run(world.ingress.ingest(world.context(conn), envelope("again", ref="m-3")))
    assert [r.message.sequence for r in (first, second, third)] == [1, 2, 3]
    assert second.message.occurred_at < first.message.occurred_at  # sequence != source order
    assert len(world.repository.conversations) == 1
    assert third.conversation.last_message_id == third.message.message_id


def test_identical_replay_is_idempotent_and_conflicts_fail_closed() -> None:
    world = ConversationWorld()
    conn = run(world.channel())
    first = run(world.ingress.ingest(world.context(conn), envelope("A")))
    again = run(world.ingress.ingest(world.context(conn), envelope("A")))
    assert again.replayed and again.message == first.message
    assert len(world.repository.messages) == 1
    for changed in (envelope("B"), envelope("A", sender="other"), envelope("A", thread="t-9"),
                    envelope("A", at=T0 + timedelta(seconds=1))):  # fmt: skip
        with pytest.raises(InboundMessageConflictError):
            run(world.ingress.ingest(world.context(conn), changed))
    (stored,) = world.repository.messages.values()
    assert stored.text == "A" and stored.sequence == 1  # never overwritten
    assert world.repository.next_sequence[stored.conversation_id] == 2  # no increment


def test_concurrent_identical_and_distinct_messages() -> None:
    world = ConversationWorld()
    conn = run(world.channel())

    async def race():
        same = [world.ingress.ingest(world.context(conn), envelope()) for _ in range(5)]
        other = [world.ingress.ingest(world.context(conn), envelope(f"n{i}", ref=f"x-{i}"))
                 for i in range(5)]  # fmt: skip
        return await asyncio.gather(*same, *other)

    results = run(race())
    assert len({r.message.message_id for r in results[:5]}) == 1
    assert [r.replayed for r in results[:5]].count(False) == 1
    sequences = sorted(m.sequence for m in world.repository.messages.values())
    assert sequences == list(range(1, 7)) and len(world.repository.conversations) == 1


def test_channel_must_be_an_enabled_installed_messaging_receiver_of_this_company() -> None:
    world = ConversationWorld()
    disabled = run(world.channel(enabled=False))
    send_only = run(world.channel(integration=MESSAGING))  # declares messages.send only
    commerce = run(world.channel(integration=COMMERCE))
    foreign = run(world.channel(company=OTHER_COMPANY))
    cases = [
        (ChannelContext(company_id=COMPANY, connection_id=uuid4()), R.CONNECTION_NOT_FOUND),
        (ChannelContext(company_id=COMPANY, connection_id=foreign.connection_id),
         R.CONNECTION_NOT_FOUND),
        (world.context(disabled), R.CONNECTION_DISABLED),
        (world.context(commerce), R.NOT_MESSAGING),
        (world.context(send_only), R.RECEIVE_NOT_SUPPORTED),
    ]  # fmt: skip
    for context, reason in cases:
        with pytest.raises(InboundMessageRefusedError) as info:
            run(world.ingress.ingest(context, envelope()))
        assert info.value.reason is reason
    # An integration that is not installed in this build (catalog changed).
    from app.integration_management import IntegrationCatalog

    world.ingress._catalog = IntegrationCatalog(())
    ok = run(world.channel())
    with pytest.raises(InboundMessageRefusedError) as info:
        run(world.ingress.ingest(world.context(ok), envelope()))
    assert info.value.reason is R.INTEGRATION_NOT_INSTALLED
    assert world.repository.messages == {} and world.repository.conversations == {}


def test_payload_identity_is_never_trusted() -> None:
    world = ConversationWorld()
    conn = run(world.channel())
    smuggled = {**envelope().model_dump(), "company_id": OTHER_COMPANY, "store_id": STORE_B,
                "connection_id": str(uuid4())}  # fmt: skip
    with pytest.raises(InboundMessageRefusedError) as info:
        run(world.ingress.ingest(world.context(conn), smuggled))
    assert info.value.reason is R.ENVELOPE_INVALID and "company" not in str(info.value)
    for bad in ({**envelope().model_dump(), "text": "x" * 16_001}, {"text": "hi"}, "raw"):
        with pytest.raises(InboundMessageRefusedError):
            run(world.ingress.ingest(world.context(conn), bad))
    # A valid mapping from an adapter is accepted (validated strictly).
    accepted = run(world.ingress.ingest(world.context(conn), envelope().model_dump()))
    assert accepted.conversation.company_id == COMPANY
    with pytest.raises(TypeError):
        run(world.ingress.ingest({"company_id": COMPANY}, envelope()))  # type: ignore[arg-type]


def test_store_binding_is_fixed_by_the_first_trusted_context() -> None:
    world = ConversationWorld()
    conn = run(world.channel())
    first = run(world.ingress.ingest(world.context(conn, STORE_A), envelope()))
    assert first.conversation.store_id == STORE_A
    with pytest.raises(InboundMessageRefusedError) as info:
        run(world.ingress.ingest(world.context(conn, STORE_B), envelope("n", ref="m-2")))
    assert info.value.reason is R.STORE_CONFLICT and len(world.repository.messages) == 1


def test_isolation_by_company_and_connection() -> None:
    world = ConversationWorld()
    mine, other_conn = run(world.channel()), run(world.channel(name="Second inbox"))
    theirs = run(world.channel(company=OTHER_COMPANY))
    results = [run(world.ingress.ingest(world.context(c), envelope()))
               for c in (mine, other_conn, theirs)]  # the SAME external refs  # fmt: skip
    assert len({r.conversation.conversation_id for r in results}) == 3
    assert all(not r.replayed and r.message.sequence == 1 for r in results)


def test_removed_connection_keeps_history_but_ingests_nothing() -> None:
    world = ConversationWorld()
    conn = run(world.channel())
    first = run(world.ingress.ingest(world.context(conn), envelope()))
    assert run(world.connections.delete(COMPANY, conn.connection_id))
    with pytest.raises(InboundMessageRefusedError) as info:
        run(world.ingress.ingest(world.context(conn), envelope("n", ref="m-2")))
    assert info.value.reason is R.CONNECTION_NOT_FOUND
    from tests.support.conversation_fakes import reader

    summary = run(world.service.get_conversation(reader(), first.conversation.conversation_id))
    assert summary.channel.connection_name is None  # label gone, history kept
    page = run(world.service.list_messages(reader(), first.conversation.conversation_id))
    assert [m.message_id for m in page.messages] == [first.message.message_id]


def test_storage_failures_fail_closed() -> None:
    world = ConversationWorld()
    conn = run(world.channel())
    world.repository.fail = True
    with pytest.raises(ConversationUnavailableError):
        run(world.ingress.ingest(world.context(conn), envelope()))
    world.repository.fail = False

    async def broken(*args, **kwargs):
        from app.integration_management.connections import ConnectionRepositoryError

        raise ConnectionRepositoryError()

    world.connections.get = broken  # type: ignore[method-assign]
    with pytest.raises(ConversationUnavailableError):
        run(world.ingress.ingest(world.context(conn), envelope()))
    assert world.repository.messages == {} and world.repository.conversations == {}


def test_untrusted_text_is_stored_inertly_and_triggers_nothing() -> None:
    world = ConversationWorld()
    conn = run(world.channel())
    for i, text in enumerate((INJECTION, SCRIPT)):
        result = run(world.ingress.ingest(world.context(conn), envelope(text, ref=f"i-{i}")))
        assert result.message.text == text
    # The ingress has no collaborator other than the repositories and the catalog: no
    # gate, coordinator, approval broker, workflow engine, agent, model or knowledge.
    assert set(vars(world.ingress)) == {"_repository", "_connections", "_catalog", "_clock",
                                        "_new_id", "_observability"}  # fmt: skip


def test_ingest_observations_are_low_cardinality() -> None:
    obs = RecordingObservability()
    world = ConversationWorld(observability=obs)
    conn = run(world.channel())
    run(world.ingress.ingest(world.context(conn), envelope(INJECTION)))
    run(world.ingress.ingest(world.context(conn), envelope(INJECTION)))
    with pytest.raises(InboundMessageConflictError):
        run(world.ingress.ingest(world.context(conn), envelope("other")))
    records = obs.of(ProductOperation.CONVERSATION_INGEST)
    assert [r.outcome for r in records] == [ObservationOutcome.COMPLETED,
                                            ObservationOutcome.COMPLETED,
                                            ObservationOutcome.CONFLICT]  # fmt: skip
    forbidden = (COMPANY, str(conn.connection_id), "thread-1", "msg-1", "sender-1", INJECTION)
    for record in obs.records:
        for value in record.attributes.values():
            assert not any(f in str(value) for f in forbidden) and len(str(value)) <= 40
