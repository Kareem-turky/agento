"""ConversationReadService (Task 037): conversations.read, store scope, company isolation
without an existence oracle, bounded lists and keyset message pagination, safe channel
labels and low-cardinality observability. Read-only by construction."""

import asyncio
from uuid import uuid4

import pytest

from app.conversations.errors import (
    ConversationAccessDeniedError,
    ConversationInputError,
    ConversationNotFoundError,
    ConversationUnavailableError,
)
from app.conversations.service import ConversationReadService
from app.observability import ObservationOutcome, ProductOperation
from tests.support.conversation_fakes import (
    COMPANY,
    INJECTION,
    OTHER_COMPANY,
    STORE_A,
    STORE_B,
    ConversationWorld,
    envelope,
    reader,
)
from tests.support.observability import RecordingObservability


def run(coroutine):
    return asyncio.run(coroutine)


def seeded(world: ConversationWorld):
    conn = run(world.channel())
    other = run(world.channel(company=OTHER_COMPANY))
    company_level = run(world.ingress.ingest(world.context(conn), envelope(thread="t-company")))
    store_a = run(world.ingress.ingest(world.context(conn, STORE_A),
                                       envelope(thread="t-a", ref="a-1")))  # fmt: skip
    store_b = run(world.ingress.ingest(world.context(conn, STORE_B),
                                       envelope(thread="t-b", ref="b-1")))  # fmt: skip
    foreign = run(world.ingress.ingest(world.context(other), envelope()))
    return conn, company_level, store_a, store_b, foreign


def test_reading_needs_conversations_read() -> None:
    world = ConversationWorld()
    seeded(world)
    for context in (reader(frozenset({"orders.read"})), reader(frozenset())):
        with pytest.raises(ConversationAccessDeniedError):
            run(world.service.list_conversations(context))
    from app.context.models import RequestContext

    with pytest.raises(ConversationAccessDeniedError):
        run(world.service.list_conversations(RequestContext(actor=None, channel="api")))


def test_lists_are_company_and_store_scoped_newest_first() -> None:
    world = ConversationWorld()
    conn, company_level, store_a, store_b, foreign = seeded(world)
    listed = run(world.service.list_conversations(reader()))  # stores: {A}
    ids = [s.conversation.conversation_id for s in listed]
    assert ids == [store_a.conversation.conversation_id, company_level.conversation.conversation_id]
    both = run(world.service.list_conversations(reader(stores=frozenset({STORE_A, STORE_B}))))
    assert len(both) == 3 and foreign.conversation.conversation_id not in {
        s.conversation.conversation_id for s in both
    }
    none = run(world.service.list_conversations(reader(stores=frozenset())))
    assert [s.conversation.store_id for s in none] == [None]
    label = listed[0].channel
    assert (label.integration_name, label.connection_name) == ("Example Chat (test)",
                                                               "Support inbox")  # fmt: skip
    only = run(world.service.list_conversations(reader(), connection_id=conn.connection_id))
    assert len(only) == 2
    assert run(world.service.list_conversations(reader(), connection_id=uuid4())) == ()


def test_foreign_and_inaccessible_conversations_are_not_found() -> None:
    world = ConversationWorld()
    _, company_level, _, store_b, foreign = seeded(world)
    assert run(world.service.get_conversation(reader(), company_level.conversation.conversation_id))
    for target in (foreign.conversation.conversation_id, store_b.conversation.conversation_id,
                   uuid4(), "not-a-uuid"):  # fmt: skip
        with pytest.raises(ConversationNotFoundError):
            run(world.service.get_conversation(reader(), target))
        with pytest.raises(ConversationNotFoundError):
            run(world.service.list_messages(reader(), target))


def test_message_pages_are_bounded_keyset_pages_in_sequence_order() -> None:
    world = ConversationWorld()
    conn = run(world.channel())
    for i in range(7):
        result = run(world.ingress.ingest(world.context(conn), envelope(f"m{i}", ref=f"r-{i}")))
    conversation_id = result.conversation.conversation_id
    page = run(world.service.list_messages(reader(), conversation_id, limit=3))
    assert [m.sequence for m in page.messages] == [5, 6, 7] and page.next_before_sequence == 5
    older = run(
        world.service.list_messages(
            reader(), conversation_id, limit=3, before_sequence=page.next_before_sequence
        )
    )
    assert [m.sequence for m in older.messages] == [2, 3, 4] and older.next_before_sequence == 2
    oldest = run(world.service.list_messages(reader(), conversation_id, before_sequence=2))
    assert [m.sequence for m in oldest.messages] == [1] and oldest.next_before_sequence is None
    for bad in (dict(limit=0), dict(limit=101), dict(limit=True), dict(before_sequence=1),
                dict(before_sequence="5")):  # fmt: skip
        with pytest.raises(ConversationInputError):
            run(world.service.list_messages(reader(), conversation_id, **bad))
    for bad in (dict(limit=101), dict(connection_id="nope")):
        with pytest.raises(ConversationInputError):
            run(world.service.list_conversations(reader(), **bad))


def test_untrusted_text_is_returned_verbatim_as_data() -> None:
    world = ConversationWorld()
    conn = run(world.channel())
    result = run(world.ingress.ingest(world.context(conn), envelope(INJECTION)))
    page = run(world.service.list_messages(reader(), result.conversation.conversation_id))
    assert [m.text for m in page.messages] == [INJECTION]


def test_storage_failure_and_missing_labels() -> None:
    world = ConversationWorld()
    conn = run(world.channel())
    run(world.ingress.ingest(world.context(conn), envelope()))
    bare = ConversationReadService(world.repository, world.service._gate)
    (summary,) = run(bare.list_conversations(reader()))
    assert (summary.channel.integration_name, summary.channel.connection_name) == (None, None)
    world.repository.fail = True
    with pytest.raises(ConversationUnavailableError):
        run(world.service.list_conversations(reader()))


def test_read_observations_are_low_cardinality() -> None:
    obs = RecordingObservability()
    world = ConversationWorld(observability=obs)
    conn = run(world.channel())
    result = run(world.ingress.ingest(world.context(conn), envelope(INJECTION)))
    run(world.service.list_conversations(reader()))
    run(world.service.list_messages(reader(), result.conversation.conversation_id))
    with pytest.raises(ConversationNotFoundError):
        run(world.service.get_conversation(reader(), uuid4()))
    reads = obs.of(ProductOperation.CONVERSATION_READ)
    assert [r.outcome for r in reads] == [ObservationOutcome.COMPLETED,
                                          ObservationOutcome.COMPLETED,
                                          ObservationOutcome.NOT_FOUND]  # fmt: skip
    forbidden = (COMPANY, str(result.conversation.conversation_id), str(conn.connection_id),
                 INJECTION, "thread-1", "operator-1")  # fmt: skip
    for record in obs.records:
        for value in record.attributes.values():
            assert not any(f in str(value) for f in forbidden) and len(str(value)) <= 40
