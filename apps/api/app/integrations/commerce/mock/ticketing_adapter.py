"""Mock ticketing adapter: canonical write requests in, canonical ``Ticket`` out.

Implements ``TicketingIntegration`` on top of the mock ticket desk, using the SAME
canonical identity as ``MockCommerceAdapter``: a canonical store UUID resolves to the
provider shop whose UUID5 matches, and the company must be that shop's account.
Tickets get canonical UUIDs from the provider ticket key (``EntityType.TICKET``); the
provider key appears only in ``ExternalReference``.

Error translation (no provider text crosses the contract):
- failure before the desk is called, unknown/mismatched company or store, or a desk
  rejection -> ``IntegrationWriteRejectedError`` (nothing was written);
- a desk timeout, any other desk failure, or a stored ticket that cannot be read back
  -> ``IntegrationWriteUncertainError`` (the write may have happened).
"""

from collections.abc import Callable
from datetime import datetime
from uuid import UUID

from app.commerce.domain import Ticket
from app.integrations.commerce.errors import (
    IntegrationDataError,
    IntegrationNotFoundError,
    IntegrationUnavailableError,
    IntegrationWriteRejectedError,
    IntegrationWriteUncertainError,
)
from app.integrations.commerce.mock.identity import MOCK_SYSTEM_ID, EntityType, canonical_id
from app.integrations.commerce.mock.mapping import map_ticket_state
from app.integrations.commerce.mock.models import MockShopRecord, MockTicketRecord
from app.integrations.commerce.mock.system import MockCommerceSystem, MockProviderDownError
from app.integrations.commerce.mock.ticket_desk import (
    MockDeskRejectedError,
    MockDeskTimeoutError,
    MockTicketDesk,
)


class MockTicketingAdapter:
    def __init__(
        self, system: MockCommerceSystem | None = None, desk: MockTicketDesk | None = None
    ) -> None:
        self._system = system if system is not None else MockCommerceSystem()
        self._desk = desk if desk is not None else MockTicketDesk()

    # ----- contract -------------------------------------------------------------

    async def create_ticket(
        self,
        *,
        company_id: UUID,
        store_id: UUID,
        title: str,
        description: str,
        correlation_id: UUID,
    ) -> Ticket:
        try:
            shop = self._shop_for(company_id, store_id)
        except MockProviderDownError as exc:
            raise IntegrationWriteRejectedError("ticket") from exc
        if shop is None:
            raise IntegrationWriteRejectedError("ticket")

        try:
            record = self._desk.open_ticket(
                account_key=shop.account_key,
                shop_key=shop.shop_key,
                subject=title,
                body=description,
                correlation_key=str(correlation_id),
            )
        except MockDeskRejectedError as exc:
            raise IntegrationWriteRejectedError("ticket") from exc
        except MockDeskTimeoutError as exc:
            raise IntegrationWriteUncertainError("ticket") from exc
        except Exception as exc:  # noqa: BLE001 - unknown desk failure after sending
            raise IntegrationWriteUncertainError("ticket") from exc

        try:
            return self._map_ticket(record)
        except (IntegrationDataError, MockProviderDownError) as exc:
            raise IntegrationWriteUncertainError("ticket") from exc

    async def get_ticket(self, ticket_id: UUID) -> Ticket:
        def read() -> Ticket:
            key = next(
                (
                    k
                    for k in self._desk.list_ticket_keys()
                    if canonical_id(EntityType.TICKET, k) == ticket_id
                ),
                None,
            )
            record = None if key is None else self._desk.fetch_ticket(key)
            if record is None:
                raise IntegrationNotFoundError("ticket", ticket_id)
            return self._map_ticket(record)

        return self._read(read)

    async def find_ticket_by_correlation(self, correlation_id: UUID) -> Ticket | None:
        def read() -> Ticket | None:
            record = self._desk.find_by_correlation(str(correlation_id))
            return None if record is None else self._map_ticket(record)

        return self._read(read)

    # ----- identity, errors and mapping --------------------------------------------

    def _shop_for(self, company_id: UUID, store_id: UUID) -> MockShopRecord | None:
        """The provider shop for a canonical store, only if it belongs to ``company_id``."""
        account_key = self._system.fetch_account().account_key
        for shop_key in self._system.list_shop_keys():
            if canonical_id(EntityType.STORE, shop_key) != store_id:
                continue
            shop = self._system.fetch_shop(shop_key)
            if (
                shop is not None
                and shop.account_key == account_key
                and canonical_id(EntityType.COMPANY, shop.account_key) == company_id
            ):
                return shop
            return None
        return None

    @staticmethod
    def _read[T](read: Callable[[], T]) -> T:
        try:
            return read()
        except MockProviderDownError as exc:
            raise IntegrationUnavailableError(MOCK_SYSTEM_ID) from exc

    def _map_ticket(self, record: MockTicketRecord) -> Ticket:
        shop = self._system.fetch_shop(record.shop_key)
        if (
            shop is None
            or shop.account_key != record.account_key
            or record.account_key != self._system.fetch_account().account_key
        ):
            raise IntegrationDataError("ticket", "references a store the provider does not have")
        try:
            return Ticket.model_validate(
                {
                    "id": canonical_id(EntityType.TICKET, record.ticket_key),
                    "company_id": canonical_id(EntityType.COMPANY, record.account_key),
                    "store_id": canonical_id(EntityType.STORE, record.shop_key),
                    "title": record.subject,
                    "description": record.body,
                    "status": map_ticket_state(record.status),
                    "created_at": datetime.fromisoformat(record.created_timestamp),
                    "external_refs": [{"system": MOCK_SYSTEM_ID, "external_id": record.ticket_key}],
                }
            )
        except (ValueError, TypeError) as exc:
            raise IntegrationDataError("ticket", "provider record failed validation") from exc
