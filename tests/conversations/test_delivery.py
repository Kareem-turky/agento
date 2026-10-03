"""Canonical delivery states (Task 037): forward transitions only, append-only events,
idempotent external event refs, conflicting duplicates fail closed, stale updates never
regress the current state, terminal delivered/failed, inbound messages never transition,
company isolation. In-memory (PostgreSQL in tests/integration)."""

import asyncio
from datetime import timedelta
from uuid import uuid4

import pytest

from app.conversations.delivery import DeliveryOutcome, DeliveryUpdate
from app.conversations.errors import (
    DeliveryEventConflictError,
    DeliveryRefusal,
    DeliveryUpdateRefusedError,
)
from app.conversations.models import AuthorKind, DeliveryState
from tests.support.conversation_fakes import COMPANY, OTHER_COMPANY, T0, ConversationWorld, envelope

D, OUT = DeliveryState, DeliveryOutcome


def run(coroutine):
    return asyncio.run(coroutine)


def outbound(world: ConversationWorld):
    conn = run(world.channel())
    first = run(world.ingress.ingest(world.context(conn), envelope()))
    message = run(
        world.repository.append_outbound(
            COMPANY,
            first.conversation.conversation_id,
            message_id=uuid4(),
            text="On its way",
            author_kind=AuthorKind.HUMAN,
            actor_id="operator-1",
            actor_type="user",
            now=T0,
        )
    )
    assert message is not None and message.delivery_state is D.PENDING and message.sequence == 2
    return first, message


def update(state: str, ref: str | None = None, seconds: int = 0) -> DeliveryUpdate:
    return DeliveryUpdate(state=state, occurred_at=T0 + timedelta(seconds=seconds),
                          external_event_ref=ref)  # fmt: skip


def test_forward_transitions_append_events() -> None:
    world = ConversationWorld()
    _, message = outbound(world)
    steps = [("accepted", "e1"), ("sent", "e2"), ("delivered", "e3")]
    for i, (state, ref) in enumerate(steps):
        result = run(world.delivery.record(COMPANY, message.message_id, update(state, ref, i)))
        assert (result.outcome, result.state) == (OUT.APPLIED, D(state))
    events = run(world.repository.delivery_events(COMPANY, message.message_id))
    assert [(e.sequence, e.state.value, e.external_event_ref) for e in events] == [
        (1, "accepted", "e1"),
        (2, "sent", "e2"),
        (3, "delivered", "e3"),
    ]
    assert world.repository.messages[message.message_id].delivery_state is D.DELIVERED


def test_duplicate_and_conflicting_external_events() -> None:
    world = ConversationWorld()
    _, message = outbound(world)
    run(world.delivery.record(COMPANY, message.message_id, update("accepted", "e1")))
    again = run(world.delivery.record(COMPANY, message.message_id, update("accepted", "e1")))
    assert (again.outcome, again.state) == (OUT.DUPLICATE, D.ACCEPTED)
    for conflicting in (update("sent", "e1"), update("accepted", "e1", seconds=5)):
        with pytest.raises(DeliveryEventConflictError):
            run(world.delivery.record(COMPANY, message.message_id, conflicting))
    assert len(world.repository.events) == 1


def test_stale_updates_never_regress_and_terminal_states_hold() -> None:
    world = ConversationWorld()
    _, message = outbound(world)
    for state in ("accepted", "delivered"):
        run(world.delivery.record(COMPANY, message.message_id, update(state)))
    for late in ("sent", "accepted", "unknown", "failed", "delivered"):
        result = run(world.delivery.record(COMPANY, message.message_id, update(late)))
        assert (result.outcome, result.state) == (OUT.STALE, D.DELIVERED)
    # Without an external event ref a stale update has no identity to remember.
    assert [e.state for e in world.repository.events] == [D.ACCEPTED, D.DELIVERED]
    assert world.repository.messages[message.message_id].delivery_state is D.DELIVERED
    # failed is terminal in v1 too; unknown can still be resolved.
    world2 = ConversationWorld()
    _, other = outbound(world2)
    assert run(world2.delivery.record(COMPANY, other.message_id, update("unknown"))).state is (
        D.UNKNOWN
    )
    assert run(world2.delivery.record(COMPANY, other.message_id, update("failed"))).state is (
        D.FAILED
    )
    assert run(world2.delivery.record(COMPANY, other.message_id,
                                      update("delivered"))).outcome is OUT.STALE  # fmt: skip


def test_refusals() -> None:
    world = ConversationWorld()
    first, message = outbound(world)
    cases = [
        (COMPANY, first.message.message_id, update("accepted"), DeliveryRefusal.NOT_OUTBOUND),
        (OTHER_COMPANY, message.message_id, update("accepted"), DeliveryRefusal.MESSAGE_NOT_FOUND),
        (COMPANY, uuid4(), update("accepted"), DeliveryRefusal.MESSAGE_NOT_FOUND),
        (COMPANY, message.message_id, update("received"), DeliveryRefusal.INVALID_STATE),
        (COMPANY, message.message_id, update("pending"), DeliveryRefusal.INVALID_STATE),
        (COMPANY, message.message_id, {"state": "bogus"}, DeliveryRefusal.INVALID_STATE),
    ]  # fmt: skip
    for company, message_id, payload, reason in cases:
        with pytest.raises(DeliveryUpdateRefusedError) as info:
            run(world.delivery.record(company, message_id, payload))
        assert info.value.reason is reason
    assert world.repository.events == []
    assert world.repository.messages[first.message.message_id].delivery_state is D.RECEIVED


def delivered(world: ConversationWorld):
    _, message = outbound(world)
    for i, (state, ref) in enumerate((("accepted", "e1"), ("delivered", "e2"))):
        run(world.delivery.record(COMPANY, message.message_id, update(state, ref, i)))
    return message


def test_a_referenced_stale_event_is_remembered_without_regressing() -> None:
    world = ConversationWorld()
    message = delivered(world)
    result = run(world.delivery.record(COMPANY, message.message_id, update("sent", "e-stale", 1)))
    assert (result.outcome, result.state) == (OUT.STALE, D.DELIVERED)
    assert world.repository.messages[message.message_id].delivery_state is D.DELIVERED
    stale = [e for e in world.repository.events if e.external_event_ref == "e-stale"]
    assert [(e.state, e.applied, e.sequence) for e in stale] == [(D.SENT, False, 3)]
    assert [(e.state, e.applied) for e in world.repository.events] == [
        (D.ACCEPTED, True),
        (D.DELIVERED, True),
        (D.SENT, False),
    ]
    # Identical replay of the stale observation: DUPLICATE, nothing new, no regression.
    again = run(world.delivery.record(COMPANY, message.message_id, update("sent", "e-stale", 1)))
    assert (again.outcome, again.state) == (OUT.DUPLICATE, D.DELIVERED)
    assert len(world.repository.events) == 3


def test_a_conflicting_replay_of_a_stale_event_fails_closed() -> None:
    world = ConversationWorld()
    message = delivered(world)
    run(world.delivery.record(COMPANY, message.message_id, update("sent", "e-stale", 1)))
    for conflicting in (update("failed", "e-stale", 1), update("sent", "e-stale", 9)):
        with pytest.raises(DeliveryEventConflictError):
            run(world.delivery.record(COMPANY, message.message_id, conflicting))
    assert len(world.repository.events) == 3
    assert world.repository.messages[message.message_id].delivery_state is D.DELIVERED


def test_applied_event_duplicates_and_conflicts_are_unchanged() -> None:
    world = ConversationWorld()
    _, message = outbound(world)
    first = run(world.delivery.record(COMPANY, message.message_id, update("accepted", "e1")))
    again = run(world.delivery.record(COMPANY, message.message_id, update("accepted", "e1")))
    assert (first.outcome, again.outcome) == (OUT.APPLIED, OUT.DUPLICATE)
    for conflicting in (update("sent", "e1"), update("accepted", "e1", 3)):
        with pytest.raises(DeliveryEventConflictError):
            run(world.delivery.record(COMPANY, message.message_id, conflicting))
    assert [(e.state, e.applied) for e in world.repository.events] == [(D.ACCEPTED, True)]


def test_an_unapplied_observation_always_carries_an_event_ref() -> None:
    from pydantic import ValidationError

    from app.conversations.delivery import MessageDeliveryEvent

    base = dict(message_id=uuid4(), company_id=COMPANY, sequence=1, state="sent",
                occurred_at=T0, recorded_at=T0)  # fmt: skip
    assert MessageDeliveryEvent(**base, applied=True).external_event_ref is None
    assert MessageDeliveryEvent(**base, applied=False, external_event_ref="e").applied is False
    with pytest.raises(ValidationError):
        MessageDeliveryEvent(**base, applied=False)
