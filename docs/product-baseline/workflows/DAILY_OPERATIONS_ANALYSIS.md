# Daily operations analysis and FulFly

> **Status:** "Existing workflow" sections are `VERIFIED_CURRENT_PRODUCT` (from
> `apps/api/app/workflows/operations_daily.py`, `apps/api/app/services/operations_reports.py`
> and [`../../WORKFLOWS.md`](../../WORKFLOWS.md)). FulFly sections are
> `OPEN_ARCHITECTURE_DECISION`. Nothing here changes the workflow.

## Existing workflow (current Core)

`DailyOperationsWorkflow` is a deterministic Workflow, not an Agent. It runs as the
durable Workflow `operations.daily_report` (version 1) behind:

- `GET /api/v1/operations/reports/daily?store_id=&business_date=` (Product route), and
- the Operations Agent read tool `get_daily_operations_report`.

Sequence:

1. **Governance preflight** — all three actions must be `ALLOW` before any read:
   `operations.store.read` (`stores.read`), `operations.orders.list` (`orders.read`),
   `operations.shipments.list` (`shipments.read`). Otherwise `403`, nothing is read.
2. `CommerceIntegration.get_store(store_id)`; the store must match the id and the
   caller's company.
3. Business day = `[local midnight, next local midnight)` in `store.timezone` (DST-safe).
4. `list_orders(OrderQuery(store_id, created_from, created_to))`.
5. `list_shipments(ShipmentQuery(store_id, shipped_from, shipped_to))`.
6. Parent orders of shipments are re-read (`get_order`) and must belong to the store.
7. Deterministic metrics (counts per `OrderStatus` and `ShipmentStatus`) and a fixed
   finding rule set: `order_status_unknown`, `shipment_failed`, `shipment_returned`,
   `shipment_status_unknown`. No thresholds, SLAs or model reasoning.

Properties that matter for FulFly:

- **Fail closed.** Any integration error, foreign/out-of-window/malformed record, or
  duplicate id fails the whole report (`503`). There is **no partial report state**.
- **Fixed coverage.** `coverage` is always `orders: created_in_business_day`,
  `shipments: shipped_in_business_day`, `inventory: not_included`
  (`store_scoped_inventory_query_unavailable`). There is no `partial`, `unverified` or
  `complete` value.
- **No report persistence.** The Workflow persists execution metadata only; the report
  is returned, not stored. There is no report id and no report read route.
- **No writes.** No model, ticket, command or ActionRun.

Permission naming: the daily report needs `stores.read`, `orders.read` and
`shipments.read`. There is no `operations.report.read` or `operations.report.run`
permission in the Product; earlier drafts of this package used those names in error.

## What a FulFly adapter would have to provide

| Workflow step | Contract call | FulFly ability | Status |
|---|---|---|---|
| 2 | `get_store` | No provider source; Product configuration | gate 2 |
| 4 | `list_orders` with a created-at window | No date filter: full paging of all orders, then local filtering; each order needs a detail read for items | gates 1, 4, 6 |
| 5 | `list_shipments` | **No shipment API** | gate 3 |
| 6 | `get_order` | `GET /orders/order` | gate 6 |

## Capability mismatch: shipments

`DailyOperationsWorkflow` requires `operations.shipments.list` and calls
`list_shipments`. FulFly documents no shipment, courier or tracking API. Therefore:

- **FulFly is not a drop-in backend** for the existing daily workflow.
- A FulFly adapter must **never fabricate** a `Shipment` (for example from
  `status == "Shipped"` or `"Delivered"`), and must not return an empty tuple to mean
  "no shipments", because the report would then state `shipments_shipped = 0` as fact.
- Raising `IntegrationUnavailableError` from `list_shipments` would make every daily
  report fail (`503`), which is honest but makes the report unusable. Whether that
  error is even the right signal for "capability not supported" is part of gate 8
  (generic `CommerceIntegration` conformance).

Two generic options are documented. **Neither is chosen in this PR**; the choice is
gate 3 of the [implementation gate](../INTEGRATION_001_DECISIONS.md#implementation-gate).

### Option A — capability-degraded daily workflow (generic)

The existing workflow learns to read the integration's declared capabilities
(`IntegrationDescriptor.capabilities`, which already distinguishes `orders_read`,
`shipments_read`, `inventory_read`). When `shipments_read` is absent it skips the
shipment reads and reports an order-only result with explicit coverage (for example
`shipments: not_available`). Requires a reviewed Core change to the workflow, the
coverage model, the report schema and the preflight. It must be generic: no FulFly
names or rules in Core.

### Option B — separate generic order-only workflow (generic)

A new, separately reviewed Workflow that only needs `operations.store.read` and
`operations.orders.list`, with its own coverage semantics. The existing daily workflow
stays unchanged. Requires a new Workflow definition (and Task/route exposure decisions).
It must be generic: usable by any order-only backend, not FulFly-specific.

Both options also need the pagination coverage decision below.

## Pagination coverage (BLOCKER)

FulFly's order list is page-based over a mutable set, with no snapshot, cursor, stable
ordering or filters (see
[`../integrations/FULFLY_CONTRACT.md`](../integrations/FULFLY_CONTRACT.md#9-time-and-pagination-semantics)).

- `received_count == totalOrders` does **not** prove completeness; skips and
  duplicates can cancel out, and `totalOrders` can change during the read.
- De-duplication by provider id hides duplicates but cannot reveal skips.
- A reconciliation pass (re-reading pages, comparing ids) reduces risk but does not
  prove completeness.
- Until FulFly confirms stable paging semantics, any FulFly-backed coverage is
  `unverified` / `partial`, never complete.

The current workflow cannot express that: it either returns a report whose coverage
reads as the whole business day, or fails. Representing unverified coverage needs a
generic Core decision (gate 4). Until then, a FulFly-backed daily report must not be
offered.

## Other FulFly-specific constraints

- Business-day filtering is local: FulFly `createdAt` values with `Z` are converted to
  the store timezone window. Values without an offset cannot be placed and make the
  record unmappable.
- Inventory is not part of the daily report today (`coverage.inventory = not_included`)
  and Integration 001 does not change that.
- `deliveredIn` is not used for any metric or finding (unit and meaning unconfirmed).
- No new metrics (delivery rate, ageing, geography, waiting-for-stock) are part of
  Integration 001. They would be `PROPOSED_FUTURE` Core changes.
