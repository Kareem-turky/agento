# FulFly Integration Contract

> **Discovery source:** FulFly API Reference at `https://fulfly.net/docs/api-reference-client/integrations-docs.html`  
> **Reviewed:** 2026-10-05  
> **Status:** `VERIFIED_PROVIDER_DOC` unless a line says otherwise. Facts below come from
> FulFly's published documentation only; nothing was validated against a live account
> (`REQUIRES_LIVE_VALIDATION` for every behaviour that matters to Agento). Integration 001
> decisions and Agento mappings are marked separately and live in
> [`../INTEGRATION_001_DECISIONS.md`](../INTEGRATION_001_DECISIONS.md) and
> [`../COMMERCE_DOMAIN.md`](../COMMERCE_DOMAIN.md). This document is the provider
> discovery source; it does not describe Agento behaviour.

## 1. Connection

- Base URL: `https://apiv2.fulfly.net/`
- Protocol: JSON over HTTPS, except the documented image-upload multipart endpoint.
- IDs: 24-character hexadecimal strings.
- Header names are case-insensitive.
- Scoping is automatic according to the authenticated account.

## 2. Authentication and currency

### Login

```http
POST /auth/login
Content-Type: application/json
currency: <currency-id>
```

```json
{
  "email": "you@example.com",
  "password": "your-password"
}
```

Response `200`:

```json
{
  "token": "<JWT>",
  "permissions": []
}
```

JWT lifetime is 12 hours. The token contains the currencies available at sign-in. Sign in again if currency access changes.

All subsequent authenticated requests use:

```http
Authorization: Bearer <token>
currency: <currency-id>
```

The documentation says the `currency` header is required on every request, including login and public endpoints. Monetary amounts (prices, costs, payments) are expressed in the selected currency. Stock quantities are counts, not currency-denominated values; the documentation does not state whether stock visibility itself differs by currency (`REQUIRES_LIVE_VALIDATION`).

The currency id is an opaque FulFly id, not an ISO 4217 code. Responses also carry a `currency` object (for example `{"_id": "...", "name": "EGP"}`); whether `name` is always an ISO 4217 code is not documented (`REQUIRES_LIVE_VALIDATION`).

## 3. Account types

| Type | Description |
|---|---|
| Seller | Supplies and manages products |
| Affiliate | Markets products through its storefront and earns commission |
| Moderator | Acts for its linked affiliate |
| Buyer | End customer |

Calling an endpoint outside the account type returns `422 Insufficient permissions`. Individual capability flags may also apply.

## 4. Orders endpoints

### `POST /orders/affiliate-order`

Role: Affiliate. Side effect: creates an order, reduces stock, adds status history, and may send WhatsApp.

Body fields:

| Field | Type | Required | Meaning |
|---|---|---:|---|
| `name` | string | yes | Customer name |
| `phone` | string | yes | Customer phone |
| `otherPhone` | string | no | Secondary phone |
| `platform` | id | no | Source platform |
| `apartment` | string | no | Address |
| `floor` | string | no | Address |
| `building` | string | no | Address |
| `street` | string | no | Address |
| `details` | string | yes | Address details |
| `govId` | id | yes | Governorate |
| `areaId` | id | no | Area |
| `direction` | string | yes | `Forward` or `Exchange` |
| `exchangeForTrack` | string | no | Track being exchanged |
| `notes` | string | no | Notes |
| `products` | array | yes | `{_id: variantId, number: quantity}` |
| `bundles` | array | yes | Bundle and variant IDs |
| `priceAdjustmentArray` | array | yes | Item price overrides |
| `isPriceAdjusted` | boolean | no | Apply overrides |
| `store` | id | no | Storefront attribution |
| `linkAffsProductsMap` | object | no | Linked-affiliate attribution |

Response `200`: `{ "_id": "<order-id>", "track": "<8-char-hex>" }`.

Integration 001 decision: excluded (no provider writes).

### `GET /orders/affiliate-orders`

Role: Affiliate.

Other headers:

- `page`: 1-indexed page number.
- `recordsPerPage`: page size; default and maximum are not documented.

Response `200`:

```json
{
  "orders": [
    {
      "_id": "...",
      "track": "...",
      "status": "Complete",
      "name": "Customer Test",
      "phone": "01000000000",
      "direction": {"value": "Forward"},
      "totalCost": {"amount": 0},
      "totalPayment": {"aff": {"amount": 0, "editHistory": []}},
      "isWaitingForStock": false,
      "paymentStatus": "paid",
      "shipping": {"govId": {"_id": "...", "englishName": "Alexandria"}},
      "createdAt": "2026-08-05T13:52:37.378Z"
    }
  ],
  "totalOrders": 2517,
  "currency": {"_id": "...", "name": "EGP"}
}
```

No date, status, update, or search filter is documented. No cursor, snapshot token, sort order or consistency guarantee across pages is documented either (see section 9).

### `PUT /orders/cancel-orders`

Roles: Affiliate, Seller, Moderator, Buyer.

Body table documents `ordersIds` required and `label` optional/reserved. The example additionally sends `status: "Cancelled"`, but `status` is missing from the parameter table. Treat this as an unresolved contract inconsistency.

Orders are eligible only in `New`, `Confirmed`, `Waiting`, or `Printed`. The call returns stock and adjusts fulfilment expenses. Integration 001 decision: excluded (no provider writes).

### `POST /orders/export-xlsx`

Roles: Affiliate, Seller. Body: required `mode`, optional `ordersIds[]`. Returns `{location}` containing a generated public XLSX URL. The endpoint creates a file and is excluded from Integration 001.

### `GET /orders/order`

Roles: Buyer, Affiliate, Seller, Moderator.

Header: `orderId` containing the order `_id`.

Response contains `{order, totalTickets, tickets, reminders}`. Documented order fields include `_id`, `track`, `barcode`, `status`, customer fields, `netPrice`, `shippingCost`, `totalCost`, `paymentStatus`, product snapshots, currency, and `createdAt`. Fields are role-scoped and therefore must be treated as optional when parsing. The field-level schema of the product snapshots (title, quantity, unit price) is not fully documented (`REQUIRES_LIVE_VALIDATION`; gate 6 in [`../INTEGRATION_001_DECISIONS.md`](../INTEGRATION_001_DECISIONS.md#implementation-gate)). The `tickets` and `reminders` here are FulFly records, unrelated to Agento operational tickets.

### `GET /orders/order-status-history`

Roles: Buyer, Affiliate, Seller, Moderator.

Header: `orderId`.

Response:

```json
{
  "history": [
    {"_id": "...", "status": "New", "createdAt": "..."},
    {"_id": "...", "status": "Complete", "createdAt": "..."}
  ]
}
```

Events are sorted oldest-first.

## 5. Product and inventory endpoints

| Method and path | Role | Contract |
|---|---|---|
| `POST /products/add-product` | Seller | Creates product; excluded (no provider writes) |
| `POST /products/add-product-variant` | Seller | Creates hidden/unapproved variant; excluded |
| `PUT /products/add-variant-images` | Seller | Multipart upload; excluded |
| `GET /products/all-product-variants` | Affiliate/Seller/Moderator | Returns unpaginated `{variants}`; item schema is undocumented |
| `GET /products/get-product-variants` | Seller | Headers `productId`, `page`; 100/page; returns variants, total, category |

The seller product-variants example documents `_id`, `price`, `availableStock`, and `isApproved`.

Contract gaps:

- The cross-product variant schema is not documented.
- There is no documented product-list endpoint that guarantees all `productId` values.
- `add-product` marks several fields required while its example omits them.
- No inventory movement, reservation, or stock timestamp is documented.

## 6. Categories and shipping reference

### `GET /categories/get-all-public-categories`

Public, but currency is still required. Optional `subCatId`; otherwise returns all available categories. Not paginated.

### `GET /shipping/get-governments`

Authenticated. Returns available governorates with `_id`, `englishName`, `cost`, `returnCost`, and `deliveredIn` for the selected currency.

`deliveredIn` MUST NOT be read as an SLA, a delivery promise or a duration until FulFly confirms its unit and semantics (`REQUIRES_LIVE_VALIDATION`). No Agento metric, finding or threshold may be derived from it before then.

### `GET /shipping/get-specific-governments-areas`

Authenticated. Header `govId`. Returns area `_id`, `englishName`, government ID, and currency ID.

These endpoints provide shipping reference data, not shipment tracking resources. They cannot produce an Agento `Shipment`.

## 7. Status webhook

Available to Seller and Affiliate accounts. Configured manually in the FulFly Profile page.

```json
{
  "orderId": "...",
  "track": "FF123456",
  "status": "Shipped",
  "updatedAt": "2026-09-28T12:00:00.000Z"
}
```

Documented behaviour:

- One POST attempt about three seconds after a supported status change.
- Five-second receiver timeout.
- No retry after failure.
- Initial creation sends no notification.
- Receiver may respond `200` with an empty body.
- `updatedAt` is notification-send time.
- No signature, shared secret, event ID, or replay protection is documented.

Webhook status values:

`New`, `Confirmed`, `Waiting`, `Printed`, `Packed`, `Shipped`, `Delivered`, `Complete`, `Return Request`, `Returned`, `Cancelled`.

## 8. Errors

Typical envelope:

```json
{
  "errArr": [{"msg": "Human-readable message"}],
  "type": "local"
}
```

`type` may be `local` or `global`. It is not a stable machine code. Some endpoints may return a bare string or `{msg}`. Multiple errors may appear.

| HTTP status | Documented meaning |
|---|---|
| `401` | Missing, malformed, or expired token |
| `404` | Login user unavailable, deactivated, or wrong password |
| `422` | Validation, permission, not found, or outside scope |
| `500` | Missing/unavailable currency, despite being a configuration error |

No rate-limit contract, `429`, or `Retry-After` behaviour is documented.

## 9. Time and pagination semantics

Timestamp examples use ISO-8601 with `Z`, but the documentation does not explicitly guarantee UTC for every timestamp or define the business timezone. Agento stores raw timestamps, parses `Z` as UTC, and defines business-day boundaries from company configuration.

`GET /orders/affiliate-orders` is page-based (1-indexed `page`, `recordsPerPage`) over a live, mutable order set. The documentation defines no snapshot, no stable sort order, no cursor and no filters. Consequences for any consumer:

- New orders created, or orders changing, while pages are read can shift records between pages, so a record can be **skipped** or **duplicated**.
- `received_count == totalOrders` therefore does **not** prove completeness: a skip and a duplicate cancel out in the count, and `totalOrders` itself may change between pages.
- De-duplicating by `_id` removes duplicates but cannot detect skips.
- A second reconciliation pass reduces risk but does not prove completeness.

Until FulFly confirms stable ordering and snapshot (or cursor) semantics, any coverage derived from this endpoint is `unverified` / `partial`, never complete (gate 4 in [`../INTEGRATION_001_DECISIONS.md`](../INTEGRATION_001_DECISIONS.md#implementation-gate)). Default and maximum `recordsPerPage` are undocumented (`REQUIRES_LIVE_VALIDATION`).

Product-specific variants use 1-based pages of 100. Cross-product variants and categories are unpaginated.

## 10. Integration 001 candidate read allowlist

> **Status:** `OPEN_ARCHITECTURE_DECISION`. This is the maximum provider surface an
> adapter could use, not an approved or implemented list. Nothing is enabled anywhere.

Candidate reads:

- `/auth/login`
- `GET /orders/affiliate-orders`
- `GET /orders/order`
- `GET /orders/order-status-history`
- `GET /shipping/get-governments`
- `GET /shipping/get-specific-governments-areas`
- `GET /products/all-product-variants` only in a contract-probe environment until its schema is verified

Never used by Integration 001 (`APPROVED_INTEGRATION_DECISION`):

- All order and product writes (FulFly Integration 001 introduces no external/provider write capability).
- XLSX export.
- Seller stock analytics unless role/product discovery is resolved.

## 11. Unavailable domain capabilities

The reviewed documentation contains no dedicated APIs for shipments, couriers, warehouses, return records, refunds, COD, or settlements. Agento must not construct those entities from names or implied meaning. In particular, an order whose `status` is `Shipped` or `Delivered` is an order status, not a shipment: no Agento `Shipment` may be created from it.

Because Agento's existing daily operations workflow requires shipment reads, FulFly is **not** a drop-in backend for it. See [`../workflows/DAILY_OPERATIONS_ANALYSIS.md`](../workflows/DAILY_OPERATIONS_ANALYSIS.md#capability-mismatch-shipments).

## 12. Questions requiring FulFly confirmation

> **Status:** `REQUIRES_LIVE_VALIDATION` / provider confirmation.

1. Integration account role and capability flags.
2. Seller/moderator order-list endpoint, if one exists.
3. Full schemas for orders, order items, and all visible variants.
4. Order page-size default and maximum, sort order, and whether paging is stable while orders change (snapshot or cursor semantics).
5. Timestamp timezone guarantee.
6. Incremental/date/status filters.
7. Accounting meaning of cost and payment fields, and the unit and meaning of `deliveredIn`.
8. Whether `track` is an order reference or shipment tracking number.
9. Rate limits.
10. Webhook authentication/signature.
11. APIs for shipment, warehouse, return, refund, COD, and settlement resources.

