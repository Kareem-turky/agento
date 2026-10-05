# FulFly → existing Core mapping analysis

> **Status:** mixed. Core model facts are `VERIFIED_CURRENT_PRODUCT` (from
> `apps/api/app/commerce/domain/` and `apps/api/app/integrations/commerce/`). FulFly
> field facts are `VERIFIED_PROVIDER_DOC` (from
> [integrations/FULFLY_CONTRACT.md](integrations/FULFLY_CONTRACT.md)). The mapping as a
> whole is **incomplete and BLOCKED** for `Order` until gates 1, 2 and 6 of the
> [implementation gate](INTEGRATION_001_DECISIONS.md#implementation-gate) close.

## Ground rules

- The existing canonical domain is the target. Integration 001 introduces **no** new
  domain model, no new status enum, no new tool, permission, route or persistence.
- Target types: `Order`, `OrderItem`, `OrderStatus`, `Shipment`, `ShipmentStatus`,
  `InventoryLevel`, `Warehouse`, `Product`, `Variant`, `Store` (plus `Money` and
  `ExternalReference`).
- The business seam is `CommerceIntegration`: `get_store`, `get_order`, `list_orders`,
  `get_shipment`, `list_shipments`, `get_inventory`, with `OrderQuery` and
  `ShipmentQuery`. It is read-only.
- Values are never invented. When a required Core field cannot be produced from
  provider data, the record cannot be mapped; the adapter raises
  `IntegrationDataError` ("nothing is coerced or guessed"), it does not fill defaults.
- Every canonical model is frozen and rejects unknown fields (`extra="forbid"`).

## Classification legend

| Class | Meaning |
|---|---|
| **direct map** | The provider value maps to a Core field without interpretation (parsing and validation only). |
| **ExternalReference** | Kept only as `ExternalReference(system, external_id)`; never the canonical `id`. |
| **source status** | Kept verbatim in `source_status`; mapped to the canonical enum separately. |
| **insufficient** | The provider supplies something, but not enough to satisfy the Core constraint on its own. |
| **unavailable** | No documented provider source. |
| **requires core extension decision** | Representing it faithfully would need a generic Core change; not done in Integration 001 without a reviewed decision. |

## `Order`

Core constraints (`apps/api/app/commerce/domain/orders.py`):

| Core field | Constraint | FulFly source | Class |
|---|---|---|---|
| `id` | `UUID`, Product-owned | `_id` (24-hex) cannot be the id | **requires decision** (gate 2: ID strategy) |
| `external_refs` | `frozenset[ExternalReference]` | `_id` → `ExternalReference("fulfly", _id)`; `track` → only if its meaning is confirmed | **ExternalReference** (`track`: open, see FULFLY_CONTRACT §12 q8) |
| `store_id` | `UUID`, required | no store-profile endpoint; `store` on create is attribution only | **requires decision** (gate 2: Store anchoring) |
| `customer_id` | `UUID \| None` | name/phone only, no customer id documented | **unavailable** → `None`; PII is not mapped into a customer |
| `status` | `OrderStatus` | `status` | see [Status mapping](#status-mapping) |
| `source_status` | optional text | `status` verbatim | **source status** |
| `items` | `tuple[OrderItem, ...]`, **min length 1** | list summaries: none. Detail (`GET /orders/order`): "product snapshots", field schema undocumented | **insufficient** from list; detail **REQUIRES_LIVE_VALIDATION** (gate 6) |
| `total` | `Money` (exact decimal + 3-letter code) | `totalCost.amount` (summary/detail) + response `currency.name` | **insufficient**: accounting meaning of `totalCost` vs `netPrice`/`totalPayment` unconfirmed; `currency.name` not documented as ISO 4217 |
| `created_at` | aware datetime | `createdAt` (ISO-8601 with `Z`) | **direct map** when the value carries `Z`/offset; a naive value cannot be mapped (UTC guarantee unconfirmed) |
| `updated_at` | aware datetime or `None` | not documented on orders | **unavailable** → `None` |

Additional invariant: every item's `unit_price.currency` must equal `total.currency`.

### Is `affiliate-orders` sufficient?

No. Order summaries from `GET /orders/affiliate-orders` contain no items, and
`Order.items` requires at least one. A Core-valid `Order` therefore needs
`GET /orders/order` for **every** order returned by `list_orders`, unless a reviewed
Core extension decision changes the contract (none is proposed here). Consequences:
one detail request per order (N+1), the per-order failure policy, and the provider's
undocumented rate limits all become part of gate 1.

Until item title, quantity, unit price and currency are proven for the chosen role,
the `Order` mapping is **BLOCKED**.

## `OrderItem`

| Core field | Constraint | FulFly source | Class |
|---|---|---|---|
| `id` | `UUID` | item `_id` if present in snapshots (undocumented) | **requires decision** (gate 2) + **REQUIRES_LIVE_VALIDATION** |
| `variant_id` | `UUID \| None` | variant `_id` if present | optional; mapped only via the ID strategy |
| `sku` | optional | not documented | **unavailable** → `None` |
| `title` | non-empty text, required | snapshot title (undocumented) | **REQUIRES_LIVE_VALIDATION** |
| `quantity` | decimal `> 0`, required | create body uses `number`; snapshot field undocumented | **REQUIRES_LIVE_VALIDATION** |
| `unit_price` | `Money`, required | snapshot price (undocumented); `priceAdjustmentArray` exists on create | **REQUIRES_LIVE_VALIDATION**; price overrides make "unit price" ambiguous |
| `external_refs` | | item/variant `_id` | **ExternalReference** |

## Status mapping

> **Status:** rows marked "candidate" are `APPROVED_INTEGRATION_DECISION` only for the
> principle (raw value preserved, no invented status); exact values remain reviewable.
> Rows marked "Core decision" are `OPEN_ARCHITECTURE_DECISION`.

Only the existing `OrderStatus` values are used: `draft`, `pending`, `confirmed`,
`processing`, `fulfilled`, `cancelled`, `completed`, `unknown`. The raw FulFly value is
always kept in `source_status`. Unknown or new raw values map to `unknown` (the daily
report raises an `order_status_unknown` finding for them).

| FulFly `status` | `OrderStatus` | Notes |
|---|---|---|
| `New` | `pending` (candidate) | Created, not yet confirmed. |
| `Confirmed` | `confirmed` (candidate) | |
| `Waiting` | `processing` (candidate) or `unknown` — **Core decision** | Waiting for stock or other hold; `isWaitingForStock` is a separate flag. No "on hold" status exists in the Core, and none is added. |
| `Printed` | `processing` (candidate) | Fulfilment preparation. |
| `Packed` | `processing` (candidate) | Fulfilment preparation. Not a shipment. |
| `Shipped` | `fulfilled` (candidate) or `unknown` — **Core decision** | An order status only. **Never** creates a `Shipment`. Whether "handed to courier" equals Core `fulfilled` must be decided. |
| `Delivered` | `fulfilled` (candidate) or `unknown` — **Core decision** | An order status only; no `Shipment.delivered_at` is produced. |
| `Complete` | `completed` (candidate) | Accounting/settlement meaning unconfirmed. |
| `Return Request` | `unknown` — **Core decision** | No return concept exists in `OrderStatus`; mapping it to `cancelled` or `completed` would misstate it. |
| `Returned` | `unknown` — **Core decision** | Same. Not a `ShipmentStatus.returned` shipment. |
| `Cancelled` | `cancelled` (candidate) | |
| anything else | `unknown` | Raw value preserved. |

`draft` has no FulFly counterpart.

## `Store`

| Core field | FulFly source | Class |
|---|---|---|
| `id`, `company_id` | none | **requires decision** (gate 2): anchored by Product configuration, never derived from provider data |
| `name` | none documented for the affiliate account | **unavailable** from provider |
| `currency` (3-letter) | `currency.name` of responses, if ISO 4217 | **REQUIRES_LIVE_VALIDATION** |
| `timezone` (IANA) | none; timestamps are `Z` | **unavailable** from provider; Product configuration |

The daily workflow requires `store.company_id` to equal the caller's scope company and
uses `store.timezone` for the business day. Neither value can come from FulFly.

## `Shipment`

| Core field | FulFly source | Class |
|---|---|---|
| all fields | no shipment, courier or tracking API | **unavailable** |

`list_shipments` and `get_shipment` have no FulFly source. Returning an empty tuple
would assert "no shipments" (false); deriving shipments from order statuses would
fabricate them. Both are forbidden. See
[workflows/DAILY_OPERATIONS_ANALYSIS.md](workflows/DAILY_OPERATIONS_ANALYSIS.md#capability-mismatch-shipments).

## `Product`, `Variant`, `InventoryLevel`, `Warehouse`

| Core type | FulFly source | Class |
|---|---|---|
| `Product` (`store_id`, `title`, `status`) | no documented product list for all roles | **unavailable** / gate 7 |
| `Variant` | `all-product-variants` (item schema undocumented) or Seller-only `get-product-variants` (`_id`, `price`, `availableStock`, `isApproved`) | **insufficient**; **REQUIRES_LIVE_VALIDATION** |
| `InventoryLevel` (`variant_id`, **`warehouse_id`** required, `available`) | `availableStock` (Seller-only) | **insufficient**: no warehouse identity |
| `Warehouse` | none | **unavailable**; a synthetic warehouse would need a Core extension decision (gate 7) |

## Fields with no Core target

| FulFly field | Treatment |
|---|---|
| `name`, `phone`, `otherPhone`, address fields | PII; not mapped. The Core `Order` has no such fields. |
| `direction` (`Forward`/`Exchange`), `exchangeForTrack` | No Core field; not mapped (would require a Core extension decision). |
| `paymentStatus`, `totalPayment`, `netPrice`, `shippingCost` | No Core field; accounting meaning unconfirmed; not mapped. |
| `isWaitingForStock` | No Core field; not mapped. |
| `shipping.govId` | Reference data only; no Core field. |
| `tickets`, `reminders` (order detail) | FulFly records; unrelated to Agento tickets; not mapped. |

## Money rules

`Money` is an exact `Decimal` with a 3-letter upper-case code; floats are rejected.
JSON numbers from FulFly must be parsed as decimals without binary rounding. There is
no exchange-rate logic. `Order.total` is taken as reported, never derived from items.
