"""The immutable scenario contract a provider adapter supplies to the conformance suite.

Everything here is CANONICAL (product UUIDs and adapter factories): no provider IDs,
records, SDKs or implementation details. A provider-specific module builds one
``CommerceConformanceFixture`` from its own test data; the generic suite only ever
talks to the adapter through ``CommerceIntegration``.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from uuid import UUID

from app.integrations.commerce import CommerceIntegration

AdapterFactory = Callable[[], CommerceIntegration]
# An adapter whose provider data for ONE entity cannot be mapped, plus that entity's id.
CorruptedFactory = Callable[[], tuple[CommerceIntegration, UUID]]


@dataclass(frozen=True)
class CommerceConformanceFixture:
    """Known canonical identities and controlled scenarios for one adapter.

    Requirements on the data behind ``adapter``:
    - stores A and B both exist and each has at least one order with a shipment;
    - ``order_a_id`` / ``shipment_a_id`` belong to store A, ``*_b_id`` to store B;
    - at least one shipment is unshipped (``shipped_at`` is None);
    - ``variant_id`` has stock in ``warehouse_id``.

    ``adapter`` must return a FRESH adapter over unchanged provider state on every
    call; the suite never mutates provider state.
    """

    name: str
    adapter: AdapterFactory
    store_a_id: UUID
    store_b_id: UUID
    order_a_id: UUID
    order_b_id: UUID
    shipment_a_id: UUID
    shipment_b_id: UUID
    variant_id: UUID
    warehouse_id: UUID
    # A known variant with no stock records (None if the provider data has none).
    variant_without_stock_id: UUID | None
    # The provider is unreachable: every read must raise IntegrationUnavailableError.
    unavailable_adapter: AdapterFactory
    # Representative unmappable provider data: must raise IntegrationDataError.
    corrupted_order: CorruptedFactory
    corrupted_shipment: CorruptedFactory | None = None
    corrupted_store: CorruptedFactory | None = None
    # Markers the fixture planted INSIDE the provider (credentials, URLs, auth headers,
    # raw payloads) that must never appear in a Product-level error.
    leak_markers: frozenset[str] = field(default_factory=frozenset)
