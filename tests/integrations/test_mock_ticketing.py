"""Mock ticketing: provider desk + adapter behind the TicketingIntegration contract."""

from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID

import pytest

from app.commerce.domain import ExternalReference, TicketStatus
from app.integrations.commerce import (
    IntegrationDataError,
    IntegrationNotFoundError,
    IntegrationUnavailableError,
    IntegrationWriteRejectedError,
    IntegrationWriteUncertainError,
    TicketingIntegration,
)
from app.integrations.commerce.mock import (
    MOCK_SYSTEM_ID,
    EntityType,
    MockCommerceAdapter,
    MockCommerceSystem,
    MockTicketDesk,
    MockTicketingAdapter,
    MockTicketWriteMode,
)
from app.integrations.commerce.mock.models import MockTicketRecord
from tests.integrations.helpers import cid, run

COMPANY = cid(EntityType.COMPANY, "acct_demo")
NORTH = cid(EntityType.STORE, "shop_north")
SOUTH = cid(EntityType.STORE, "shop_south")
CORR = UUID("11111111-2222-4333-8444-555555555555")


def make(mode: MockTicketWriteMode = MockTicketWriteMode.NORMAL, **kw):
    desk = MockTicketDesk(mode=mode, **kw)
    return MockTicketingAdapter(MockCommerceSystem(), desk), desk


def create(adapter, *, company=COMPANY, store=NORTH, corr=CORR, title="T", description="D"):
    return run(
        adapter.create_ticket(
            company_id=company, store_id=store, title=title, description=description,
            correlation_id=corr,
        )
    )  # fmt: skip


def test_adapter_satisfies_the_contract() -> None:
    assert isinstance(MockTicketingAdapter(), TicketingIntegration)


def test_create_returns_a_canonical_ticket_with_shared_store_identity() -> None:
    adapter, desk = make()
    ticket = create(adapter, title=" Late parcel ", description="Check courier")
    store = run(MockCommerceAdapter().get_store(NORTH))
    assert ticket.store_id == store.id == NORTH  # the SAME canonical store universe
    assert ticket.company_id == store.company_id == COMPANY
    assert ticket.title == "Late parcel" and ticket.description == "Check courier"
    assert ticket.status is TicketStatus.OPEN
    assert ticket.created_at == datetime(2026, 3, 2, 9, 30, tzinfo=UTC)
    provider_key = f"tkt_{CORR}"
    assert ticket.id == cid(EntityType.TICKET, provider_key)
    assert ticket.external_refs == frozenset(
        {ExternalReference(system=MOCK_SYSTEM_ID, external_id=provider_key)}
    )
    assert str(ticket.id) != provider_key and provider_key not in ticket.model_dump_json(
        exclude={"external_refs"}
    )
    assert desk.ticket_count == 1


def test_identity_is_deterministic_and_correlation_does_not_duplicate() -> None:
    first_adapter, first_desk = make()
    second_adapter, _ = make()
    one = create(first_adapter)
    assert create(second_adapter) == one  # same correlation -> same canonical ticket
    assert create(first_adapter) == one
    assert first_desk.ticket_count == 1
    other = create(first_adapter, corr=UUID(int=5))
    assert other.id != one.id and first_desk.ticket_count == 2


def test_independent_reads() -> None:
    adapter, _ = make()
    ticket = create(adapter)
    reader = adapter  # same provider state, separate read calls
    assert run(reader.get_ticket(ticket.id)) == ticket
    assert run(reader.find_ticket_by_correlation(CORR)) == ticket
    assert run(reader.find_ticket_by_correlation(UUID(int=9))) is None
    with pytest.raises(IntegrationNotFoundError):
        run(reader.get_ticket(UUID(int=9)))


def test_injectable_clock() -> None:
    adapter, _ = make(clock=lambda: "2030-01-01T00:00:00+02:00")
    assert create(adapter).created_at.isoformat() == "2030-01-01T00:00:00+02:00"


@pytest.mark.parametrize(
    ("company", "store"),
    [(COMPANY, UUID(int=404)), (UUID(int=404), NORTH), (NORTH, NORTH)],
)
def test_unknown_or_mismatched_scope_is_rejected_without_a_write(company, store) -> None:
    adapter, desk = make()
    with pytest.raises(IntegrationWriteRejectedError) as info:
        create(adapter, company=company, store=store)
    assert info.value.effect_may_have_occurred is False
    assert desk.ticket_count == 0


def test_confirmed_no_effect_mode() -> None:
    adapter, desk = make(MockTicketWriteMode.CONFIRMED_NO_EFFECT)
    with pytest.raises(IntegrationWriteRejectedError) as info:
        create(adapter)
    assert info.value.effect_may_have_occurred is False
    assert "422" not in str(info.value)
    assert desk.ticket_count == 0


def test_uncertain_after_write_mode_stores_the_ticket_then_fails() -> None:
    adapter, desk = make(MockTicketWriteMode.UNCERTAIN_AFTER_WRITE)
    with pytest.raises(IntegrationWriteUncertainError) as info:
        create(adapter)
    assert info.value.effect_may_have_occurred is True
    assert "timed out" not in str(info.value)
    assert desk.ticket_count == 1
    found = run(adapter.find_ticket_by_correlation(CORR))
    assert found is not None and found.title == "T"


def test_altered_on_write_mode_stores_different_content() -> None:
    adapter, _ = make(MockTicketWriteMode.ALTERED_ON_WRITE)
    ticket = create(adapter, title="Late parcel")
    assert ticket.title != "Late parcel"


def test_provider_outage_before_write_is_rejected_and_reads_are_unavailable() -> None:
    desk = MockTicketDesk()
    down = MockTicketingAdapter(MockCommerceSystem().with_availability(False), desk)
    with pytest.raises(IntegrationWriteRejectedError):
        create(down)
    assert desk.ticket_count == 0
    create(MockTicketingAdapter(MockCommerceSystem(), desk))
    with pytest.raises(IntegrationUnavailableError):
        run(down.find_ticket_by_correlation(CORR))


def test_unknown_provider_status_maps_to_unknown_and_foreign_records_are_rejected() -> None:
    adapter, desk = make()
    ticket = create(adapter)
    record = desk.fetch_ticket(f"tkt_{CORR}")
    assert record is not None
    desk.put_record(replace(record, status="escalated_by_bot"))
    assert run(adapter.get_ticket(ticket.id)).status is TicketStatus.UNKNOWN
    desk.put_record(replace(record, shop_key="shop_elsewhere"))
    with pytest.raises(IntegrationDataError):
        run(adapter.get_ticket(ticket.id))
    desk.put_record(replace(record, created_timestamp="2026-03-02T09:30:00"))  # naive
    with pytest.raises(IntegrationDataError):
        run(adapter.find_ticket_by_correlation(CORR))


def test_ticket_desk_does_not_touch_the_read_dataset() -> None:
    system = MockCommerceSystem()
    before = (system.search_orders(), system.search_shipments(), system.list_shop_keys())
    adapter = MockTicketingAdapter(system, MockTicketDesk())
    for n in range(3):
        create(adapter, corr=UUID(int=n + 1))
    assert (system.search_orders(), system.search_shipments(), system.list_shop_keys()) == before


def test_provider_record_is_not_a_canonical_model() -> None:
    assert not hasattr(MockTicketRecord, "model_validate")
