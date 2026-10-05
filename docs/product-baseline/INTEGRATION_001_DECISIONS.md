# Integration 001 — decision matrix and implementation gate

> **Status:** mixed. Each row and gate item carries its own label (see the
> [status vocabulary](README.md#status-vocabulary)). Current-contract columns are
> `VERIFIED_CURRENT_PRODUCT`, derived from code; FulFly columns are
> `VERIFIED_PROVIDER_DOC`.

## Capability decision matrix

"Current Agento contract" is the existing `CommerceIntegration` seam
(`apps/api/app/integrations/commerce/contract.py`), the canonical domain
(`apps/api/app/commerce/domain/`) and the daily workflow
(`apps/api/app/workflows/operations_daily.py`).

| Capability | FulFly docs | Current Agento contract | Status |
|---|---|---|---|
| Store profile | No store-profile read endpoint. A `store` id appears only as storefront attribution on order creation. | `get_store(store_id) -> Store` (`id`, `company_id`, `name`, `currency`, `timezone`). The daily workflow requires it and checks `company_id`. | `OPEN_ARCHITECTURE_DECISION` — the Store must be anchored by Product configuration, not invented from provider data (gate 2). |
| List orders | `GET /orders/affiliate-orders`, Affiliate only, page-based (`page`, `recordsPerPage` headers), returns summaries + `totalOrders`. No date/status/update filter, no cursor, no snapshot, no documented ordering. | `list_orders(OrderQuery) -> tuple[Order, ...]`; `OrderQuery` filters `store_id`, `statuses`, half-open `created_from`/`created_to`, `limit` ≤ 500; results sorted by `created_at`, then `id`. | `OPEN_ARCHITECTURE_DECISION` (gates 1, 4) + `REQUIRES_LIVE_VALIDATION`. Filtering would be local, after full paging. |
| Get order | `GET /orders/order` (`orderId` header) returns order, tickets, reminders; fields are role-scoped; item ("product snapshot") schema not fully documented. | `get_order(UUID) -> Order`; `Order.items` requires ≥ 1 `OrderItem` with `title`, `quantity > 0`, `unit_price: Money`; `total: Money`; aware `created_at`. | `REQUIRES_LIVE_VALIDATION` (gate 6). Mapping is **blocked** until items and money are proven. |
| Order status | 11 documented values (see [COMMERCE_DOMAIN.md](COMMERCE_DOMAIN.md#status-mapping)). | `OrderStatus`: `draft`, `pending`, `confirmed`, `processing`, `fulfilled`, `cancelled`, `completed`, `unknown`; raw value kept in `source_status`. | Partial mapping `APPROVED_INTEGRATION_DECISION`; ambiguous values `OPEN_ARCHITECTURE_DECISION`. |
| Order status history | `GET /orders/order-status-history`, oldest-first. | No history method on the contract. | Adapter-internal at most; no Core change in Integration 001. |
| Shipments | **No shipment, courier or tracking API.** `track` meaning unconfirmed. Shipping endpoints are reference data (governorates, areas). | `get_shipment`, `list_shipments(ShipmentQuery)`; the daily workflow **requires** `operations.shipments.list` and calls `list_shipments`. | `OPEN_ARCHITECTURE_DECISION` (gate 3). Shipments must never be fabricated. |
| Inventory | `GET /products/all-product-variants` (schema undocumented); `GET /products/get-product-variants` Seller-only, per product, 100/page. | `get_inventory(variant_id, warehouse_id?) -> tuple[InventoryLevel, ...]`; `InventoryLevel` needs `variant_id` and `warehouse_id` UUIDs. The daily report never includes inventory (`coverage.inventory = not_included`). | `OPEN_ARCHITECTURE_DECISION` (gate 7) + `REQUIRES_LIVE_VALIDATION`. |
| Warehouses | Not documented. | `Warehouse` model; `InventoryLevel.warehouse_id` is required. | Unavailable from FulFly; gate 7. |
| Connection lifecycle | `POST /auth/login` (email, password, `currency` header) → JWT (12 h). | `IntegrationDefinition` + `IntegrationConnectionDriver` (`validate_config`, `test_connection`, `aclose`) + `IntegrationSecretStore`. Production catalog is empty. | `OPEN_ARCHITECTURE_DECISION` (gate 5) for credential source and role. |
| Business backend selection | n/a | `APP_BUSINESS_BACKEND` → `BusinessBackendRegistry` (allowlist is `{"mock"}`; inputs via `BusinessBackendInputs`). Connections do not change the business backend. | `OPEN_ARCHITECTURE_DECISION` (gate 5). |
| Outbound HTTP | HTTPS JSON; custom headers (`currency`, `page`, `recordsPerPage`, `orderId`, `govId`). | `app/integrations/http/` (`IntegrationHttpTransport`, `IntegrationHttpPolicy`), not wired to any provider. | `VERIFIED_CURRENT_PRODUCT`; use is a future implementation detail. |
| Status webhook | Unsigned, single attempt, no retry, no event id. | No webhook or ingest route exists. | `PROPOSED_FUTURE` (separate reviewed task). |
| Provider writes | Order create/cancel, product create, image upload, XLSX export. | `CommerceIntegration` is read-only; the only business write is the internal ticket (`operations.ticket.create`) via the ticketing contract. | `APPROVED_INTEGRATION_DECISION`: no provider write in Integration 001. |

## Implementation gate

Coding of any FulFly adapter, driver, registration or workflow change **must not start**
until all seven decisions below are closed, reviewed and recorded here with the label
`APPROVED_INTEGRATION_DECISION`. All seven are currently `OPEN_ARCHITECTURE_DECISION`.

1. **Order completeness.** Can every `Order` field the Core requires be produced for
   every order without inventing values? This includes whether `GET /orders/order` is
   required per order (list summaries carry no items), how `Money` currency is derived,
   and what happens when one order cannot be mapped (the current workflow fails the whole
   report closed). See [COMMERCE_DOMAIN.md](COMMERCE_DOMAIN.md).
2. **ID strategy.** Persistent identity mapping vs a reviewed deterministic
   Product-owned UUID scheme, including how the `Store` (and its `company_id`,
   `currency`, `timezone`) is anchored. Requirements: same entity → same UUID; no
   collisions across entity types; a provider id is never the canonical id.
3. **Shipment capability.** FulFly has no shipment API, and the daily workflow requires
   shipments. Choose Option A, Option B or "not supported" (see
   [workflows/DAILY_OPERATIONS_ANALYSIS.md](workflows/DAILY_OPERATIONS_ANALYSIS.md#capability-mismatch-shipments)).
   Neither option may be FulFly-specific.
4. **Pagination coverage.** How coverage is represented when page-based listing cannot
   prove completeness, and whether FulFly can confirm stable ordering/snapshot semantics.
   Until then coverage is `unverified`/`partial`, never complete. The current
   `DailyOperationsCoverage` has no such value, so this also needs a generic Core decision.
5. **Credential role and capability flags.** Which FulFly account role (Affiliate is
   required for the documented order list), which capability flags, the currency, and
   which existing Product mechanism supplies credentials to the business adapter
   (business backend inputs vs integration connection secrets).
6. **Live order-detail and items validation.** A controlled live check that
   `GET /orders/order` returns item title, quantity, unit price and currency for the
   chosen role, plus page-size limits and timestamp timezone.
7. **Inventory usability.** Whether any FulFly inventory read can satisfy
   `InventoryLevel` (variant and warehouse identity) for the chosen role, or whether
   inventory stays out of scope for Integration 001.
