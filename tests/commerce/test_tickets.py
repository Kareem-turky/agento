"""Canonical operational Ticket."""

import json
from datetime import UTC, datetime
from uuid import UUID

import pytest
from pydantic import ValidationError

from app.commerce.domain import ExternalReference, Ticket, TicketStatus

BASE = {
    "id": UUID(int=1),
    "company_id": UUID(int=2),
    "store_id": UUID(int=3),
    "title": "  Late parcel  ",
    "description": "Customer reports a late parcel.",
    "status": "open",
    "created_at": datetime(2026, 3, 2, 9, 30, tzinfo=UTC),
    "external_refs": [
        {"system": "b-system", "external_id": "2"},
        {"system": "a-system", "external_id": "1"},
    ],
}


def test_status_vocabulary() -> None:
    assert [s.value for s in TicketStatus] == ["open", "resolved", "cancelled", "unknown"]


def test_ticket_is_valid_trimmed_and_frozen() -> None:
    ticket = Ticket.model_validate(BASE)
    assert ticket.title == "Late parcel" and ticket.status is TicketStatus.OPEN
    assert ExternalReference(system="a-system", external_id="1") in ticket.external_refs
    with pytest.raises(ValidationError):
        ticket.title = "changed"  # type: ignore[misc]
    assert hash(ticket) == hash(Ticket.model_validate(BASE))


@pytest.mark.parametrize(
    "change",
    [
        {"title": "   "},
        {"description": ""},
        {"status": "escalated"},
        {"created_at": datetime(2026, 3, 2, 9, 30)},  # naive
        {"company_id": "not-a-uuid"},
        {"priority": "high"},  # unknown fields are rejected
    ],
)
def test_invalid_tickets_are_rejected(change: dict) -> None:
    with pytest.raises(ValidationError):
        Ticket.model_validate({**BASE, **change})


def test_serialization_is_deterministic_and_round_trips() -> None:
    ticket = Ticket.model_validate(BASE)
    dumped = ticket.model_dump(mode="json")
    assert [r["system"] for r in dumped["external_refs"]] == ["a-system", "b-system"]
    assert Ticket.model_validate_json(json.dumps(dumped)) == ticket
    assert ticket.model_dump_json() == Ticket.model_validate(BASE).model_dump_json()


def test_ticket_fields_are_provider_independent() -> None:
    assert set(Ticket.model_fields) == {
        "id", "company_id", "store_id", "title", "description", "status", "created_at",
        "external_refs",
    }  # fmt: skip
