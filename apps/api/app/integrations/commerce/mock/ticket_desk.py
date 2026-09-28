"""The mock external ticket desk (the provider side of ticketing).

In-memory, deterministic and separate from the read-only commerce dataset, so writes
never mutate order, shipment, inventory or store data. It speaks provider-shaped
records (``MockTicketRecord``), raises its own provider errors, and never returns
canonical models.

Identity: the provider ticket key is derived from the caller's correlation key
(``tkt_<correlation>``), so the same correlation always addresses the same ticket and
a repeated create never produces a duplicate. Time comes from an injectable clock
(a fixed instant by default); there is no randomness and no wall clock.

Write modes (deterministic test behaviour):
- NORMAL: the ticket is stored and returned.
- CONFIRMED_NO_EFFECT: nothing is stored; the desk rejects the request.
- UNCERTAIN_AFTER_WRITE: the ticket IS stored, then the desk times out before
  answering, so the caller gets no receipt.
- ALTERED_ON_WRITE: the ticket is stored with a subject the desk rewrote, so an
  independent re-read does not match what was requested.
"""

from collections.abc import Callable
from enum import StrEnum

from app.integrations.commerce.mock.models import MockTicketRecord

MOCK_TICKET_TIMESTAMP = "2026-03-02T09:30:00+00:00"
NEW_TICKET_STATE = "new"


class MockTicketWriteMode(StrEnum):
    NORMAL = "normal"
    CONFIRMED_NO_EFFECT = "confirmed_no_effect"
    UNCERTAIN_AFTER_WRITE = "uncertain_after_write"
    ALTERED_ON_WRITE = "altered_on_write"


class MockDeskRejectedError(Exception):
    """Provider-specific rejection: the desk did not store anything."""


class MockDeskTimeoutError(Exception):
    """Provider-specific timeout after the request reached the desk."""


def _fixed_clock() -> str:
    return MOCK_TICKET_TIMESTAMP


class MockTicketDesk:
    def __init__(
        self,
        *,
        mode: MockTicketWriteMode = MockTicketWriteMode.NORMAL,
        clock: Callable[[], str] = _fixed_clock,
    ) -> None:
        self.mode = mode
        self._clock = clock
        self._records: dict[str, MockTicketRecord] = {}

    def open_ticket(
        self, *, account_key: str, shop_key: str, subject: str, body: str, correlation_key: str
    ) -> MockTicketRecord:
        if self.mode is MockTicketWriteMode.CONFIRMED_NO_EFFECT:
            raise MockDeskRejectedError("desk rejected the ticket (HTTP 422 from desk)")
        ticket_key = f"tkt_{correlation_key}"
        record = self._records.get(ticket_key)
        if record is None:
            if self.mode is MockTicketWriteMode.ALTERED_ON_WRITE:
                subject = f"{subject} [edited by desk]"
            record = MockTicketRecord(
                ticket_key=ticket_key,
                account_key=account_key,
                shop_key=shop_key,
                subject=subject,
                body=body,
                status=NEW_TICKET_STATE,
                correlation_key=correlation_key,
                created_timestamp=self._clock(),
            )
            self._records[ticket_key] = record
        if self.mode is MockTicketWriteMode.UNCERTAIN_AFTER_WRITE:
            raise MockDeskTimeoutError("desk timed out after accepting the ticket")
        return record

    def fetch_ticket(self, ticket_key: str) -> MockTicketRecord | None:
        return self._records.get(ticket_key)

    def list_ticket_keys(self) -> tuple[str, ...]:
        return tuple(sorted(self._records))

    def find_by_correlation(self, correlation_key: str) -> MockTicketRecord | None:
        return next(
            (r for r in self._records.values() if r.correlation_key == correlation_key), None
        )

    def put_record(self, record: MockTicketRecord) -> None:
        """Seed or overwrite a provider record directly (tests: corrupt or foreign data)."""
        self._records[record.ticket_key] = record

    @property
    def ticket_count(self) -> int:
        return len(self._records)
