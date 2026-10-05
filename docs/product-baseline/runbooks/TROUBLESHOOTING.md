# Troubleshooting notes for a future FulFly adapter

> **Status:** `PROPOSED_FUTURE`. No FulFly adapter exists. These notes record the
> provider behaviours an operator would need to recognise. Product-wide operations
> (health, readiness, system status, backup, restore) are in
> [`../../PRODUCTION_OPERATIONS.md`](../../PRODUCTION_OPERATIONS.md).

## Connection test fails

Run the generic `POST /api/v1/integrations/connection/test?connection_id=`; it records a
fixed, safe classification. Likely FulFly causes:

- `404` on login: user unavailable, deactivated or wrong password.
- `500 Invalid currency`: the `currency_id` is wrong or the account cannot use it. This is
  a configuration error; do not retry repeatedly. Sign in again after currency access
  changes (the JWT carries the currencies available at sign-in).
- `422 Insufficient permissions`: wrong FulFly account role or capability flag. The
  documented order list needs an Affiliate account.

Correct credentials only through `PUT /api/v1/integrations/connection/credentials`.
Never paste credentials or JWTs into tickets, chats or logs.

## Daily report unavailable

The daily workflow fails closed (`503`) on any integration error or unmappable record.
With FulFly it is also blocked by design until gates 3 (shipments) and 4 (pagination)
close. See [`../workflows/DAILY_OPERATIONS_ANALYSIS.md`](../workflows/DAILY_OPERATIONS_ANALYSIS.md).

## Order counts look wrong

FulFly's order list is page-based over a changing set. A count that matches
`totalOrders` does not prove completeness, and a mismatch does not identify which orders
are missing. Re-reading reduces risk but proves nothing. Treat coverage as
`unverified`/`partial` until FulFly confirms stable paging.

## Order cannot be mapped

`IntegrationDataError` means the provider record lacks something the Core requires
(items, unit price, currency, an aware timestamp) or has an unknown shape. Do not patch
data or add defaults; capture a redacted example and review the mapping.

## Unexpected shipment questions

FulFly has no shipment API. An order in `Shipped` or `Delivered` is an order status,
not a shipment; no courier, tracking or delivery date is available.

## Webhooks

Not used. The Product has no webhook route, and FulFly webhooks are unsigned and
single-attempt.
