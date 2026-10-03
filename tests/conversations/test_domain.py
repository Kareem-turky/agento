"""Canonical Conversation domain (Task 037): opaque bounded external refs, plain text
up to 16,000 characters, direction/state consistency, deterministic inbound
fingerprints and the exact outbound delivery transition graph. Pure: no I/O."""

from datetime import timedelta
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.conversations.delivery import TRANSITIONS, UPDATE_STATES, is_transition
from app.conversations.models import (
    MAX_TEXT_CHARS,
    AuthorKind,
    ChannelContext,
    ConversationMessage,
    DeliveryState,
    InboundMessageEnvelope,
    MessageDirection,
    inbound_fingerprint,
)
from tests.support.conversation_fakes import COMPANY, INJECTION, SCRIPT, T0, envelope

D = DeliveryState


def message(**changes) -> ConversationMessage:
    base = dict(
        message_id=uuid4(), conversation_id=uuid4(), company_id=COMPANY, connection_id=uuid4(),
        sequence=1, direction="inbound", author_kind="external", external_message_ref="m-1",
        text="hello", content_fingerprint="a" * 64, occurred_at=T0, recorded_at=T0,
        delivery_state="received",
    )  # fmt: skip
    return ConversationMessage.model_validate({**base, **changes})


def test_vocabularies_are_exact_and_provider_independent() -> None:
    assert [d.value for d in MessageDirection] == ["inbound", "outbound"]
    assert [a.value for a in AuthorKind] == ["external", "human", "agent", "system"]
    assert [s.value for s in D] == ["received", "pending", "accepted", "sent", "delivered",
                                    "failed", "unknown"]  # fmt: skip


def test_external_refs_are_opaque_bounded_printable_and_case_sensitive() -> None:
    ok = "AbC-123/_:.@+=~!?#[]"
    assert envelope(thread=ok, ref=ok).external_conversation_ref == ok
    assert envelope(ref="ABC").external_message_ref != envelope(ref="abc").external_message_ref
    for bad in ("", " ", "has space", "tab\there", "x" * 257, "é", "new\nline", "\x00"):
        with pytest.raises(ValidationError):
            envelope(ref=bad)
        with pytest.raises(ValidationError):
            envelope(thread=bad)
    assert envelope(ref="x" * 256).external_message_ref == "x" * 256


def test_text_is_plain_bounded_and_kept_verbatim() -> None:
    assert envelope(INJECTION).text == INJECTION and envelope(SCRIPT).text == SCRIPT
    assert envelope("سلام\tworld\nline 2").text == "سلام\tworld\nline 2"
    assert len(envelope("x" * MAX_TEXT_CHARS).text) == 16_000
    for bad in ("", "   ", "x" * (MAX_TEXT_CHARS + 1), "bell\x07", "nul\x00"):
        with pytest.raises(ValidationError):
            envelope(bad)


def test_the_envelope_carries_no_trusted_identity() -> None:
    fields = set(InboundMessageEnvelope.model_fields)
    assert fields == {"external_conversation_ref", "external_message_ref",
                      "external_sender_ref", "text", "occurred_at"}  # fmt: skip
    for smuggled in ("company_id", "store_id", "connection_id", "raw", "payload", "headers"):
        with pytest.raises(ValidationError):
            InboundMessageEnvelope.model_validate({**envelope().model_dump(), smuggled: "x"})
    assert set(ChannelContext.model_fields) == {"company_id", "connection_id", "store_id"}
    with pytest.raises(ValidationError):  # a naive source time is refused
        envelope(at=T0.replace(tzinfo=None))


def test_models_have_no_provider_or_payload_fields() -> None:
    from app.conversations.models import Conversation

    for model in (Conversation, ConversationMessage, InboundMessageEnvelope):
        for name in model.model_fields:
            for word in (
                "payload",
                "raw",
                "header",
                "token",
                "metadata",
                "json",
                "provider",
                "whatsapp",
                "phone",
                "email",
                "customer",
                "media",
                "attachment",
            ):
                assert word not in name, (model.__name__, name)
        assert model.model_config.get("frozen") and model.model_config.get("extra") == "forbid"


def test_direction_state_and_author_are_consistent() -> None:
    assert message().delivery_state is D.RECEIVED
    for bad in (dict(delivery_state="pending"), dict(author_kind="agent"),
                dict(external_message_ref=None),
                dict(created_by_actor_id="u", created_by_actor_type="user")):  # fmt: skip
        with pytest.raises(ValidationError):
            message(**bad)
    out = message(direction="outbound", author_kind="human", delivery_state="pending",
                  external_message_ref=None, created_by_actor_id="u",
                  created_by_actor_type="user")  # fmt: skip
    assert out.direction is MessageDirection.OUTBOUND
    with pytest.raises(ValidationError):
        message(direction="outbound", author_kind="human", delivery_state="received")
    with pytest.raises(ValidationError):
        message(sequence=0)


def test_inbound_fingerprint_is_deterministic_and_binds_the_content() -> None:
    connection = uuid4()
    reference = inbound_fingerprint(connection, envelope())
    assert len(reference) == 64 and reference == inbound_fingerprint(connection, envelope())
    # The same instant in another timezone is the same content.
    shifted = envelope(at=T0.astimezone(__import__("datetime").timezone(timedelta(hours=3))))
    assert inbound_fingerprint(connection, shifted) == reference
    for changed in (envelope("other"), envelope(thread="t-2"), envelope(ref="m-2"),
                    envelope(sender="s-2"), envelope(sender=None),
                    envelope(at=T0 + timedelta(seconds=1))):  # fmt: skip
        assert inbound_fingerprint(connection, changed) != reference
    assert inbound_fingerprint(uuid4(), envelope()) != reference


def test_outbound_delivery_transition_graph_is_exact() -> None:
    expected = {
        D.PENDING: {D.ACCEPTED, D.FAILED, D.UNKNOWN},
        D.ACCEPTED: {D.SENT, D.DELIVERED, D.FAILED, D.UNKNOWN},
        D.SENT: {D.DELIVERED, D.FAILED, D.UNKNOWN},
        D.UNKNOWN: {D.ACCEPTED, D.SENT, D.DELIVERED, D.FAILED},
        D.DELIVERED: set(), D.FAILED: set(), D.RECEIVED: set(),
    }  # fmt: skip
    assert {k: set(v) for k, v in TRANSITIONS.items()} == expected
    assert UPDATE_STATES == {D.ACCEPTED, D.SENT, D.DELIVERED, D.FAILED, D.UNKNOWN}
    assert not is_transition(D.DELIVERED, D.SENT)  # never regresses
    assert not any(is_transition(D.RECEIVED, s) for s in D)  # inbound never transitions
    assert not any(is_transition(s, s) for s in D)  # a repeat is not a transition
