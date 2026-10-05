# Test strategy for a future FulFly adapter

> **Status:** `PROPOSED_FUTURE`. Existing test infrastructure named here is
> `VERIFIED_CURRENT_PRODUCT`. No FulFly test exists, and none is added by this PR.

## Rules

- **No live calls in CI.** All FulFly tests use synthetic fixtures with no production
  PII or credentials. Live checks are opt-in, manual, and never part of the CI gate.
- **The Core is the oracle.** Tests assert canonical outputs (`Order`, `OrderItem`,
  `Money`, `ExternalReference`, contract errors), not provider shapes.
- **No fabrication.** Tests prove the adapter refuses rather than invents.

## 1. Core conformance (gate 8)

Current facts (`VERIFIED_CURRENT_PRODUCT`):

- The existing harness (`tests/commerce_conformance/`) is the canonical
  **full-capability** baseline. The `mock` adapter passes it
  (`tests/integrations/test_mock_conformance.py`).
- As implemented, it requires `descriptor.capabilities` to equal all read capabilities
  (`orders_read`, `shipments_read`, `inventory_read`), runs every shipment and inventory
  check unconditionally, and its fixture requires orders with shipments, shipment ids,
  a variant, a warehouse and stock.
- An order-only or otherwise partial-capability adapter therefore cannot pass it as
  written.

Requirements once gate 8 is decided (`OPEN_ARCHITECTURE_DECISION` until then):

- Gate 8 decides the generic, capability-aware conformance model, with no
  FulFly-specific branches in the harness.
- Every capability a FulFly adapter advertises must pass all applicable generic checks.
- Every capability it does not advertise must be tested to fail closed and must never
  masquerade as successful empty data (for example `list_shipments` returning `()`).
- The full-capability baseline keeps applying unchanged to full-capability adapters.

## 2. Provider adapter contract tests

- Request shape: method, path, `Authorization`, `currency` on every request, `page`,
  `recordsPerPage`, `orderId` headers.
- Login, JWT reuse within validity, re-authentication after `401`, bounded attempts.
- Error envelopes: `errArr`/`type`, bare string, `{msg}`, multiple errors; `401`,
  `404` (login), `422`, `500 Invalid currency` → the right contract error.
- Mapping: every row of [COMMERCE_DOMAIN.md](../COMMERCE_DOMAIN.md); raw status in
  `source_status`; unknown statuses → `unknown`; FulFly ids only in `ExternalReference`.
- Refusal: missing items, missing/ambiguous money, non-ISO currency, naive timestamps
  and unknown shapes raise `IntegrationDataError`.
- Identity: the same FulFly record maps to the same UUID across runs; different entity
  types never collide.
- Secrets: JWT, password and `Authorization` never appear in logs, errors, audit or
  model context.

## 3. Capability-mismatch tests

- `list_shipments` / `get_shipment` never return shipments derived from order statuses
  (`Shipped`, `Delivered`), and never an empty tuple presented as "no shipments".
- The unsupported-method behaviour is whatever gate 8 decides, tested through the
  generic capability-aware harness.
- Whatever gate 3 decides (Option A, Option B or unsupported) is tested end to end
  through the existing daily report route and the Operations Agent tools.
- `inventory_read` is advertised only if `get_inventory` satisfies `InventoryLevel`
  (gate 7) and the applicable generic checks (gate 8).

## 4. Mutable-pagination tests

Fixtures simulate a live order set changing while pages are read:

- an order inserted at the head during paging (records shift → one skipped, one
  duplicated, count still equals `totalOrders`);
- `totalOrders` changing between pages;
- duplicates across pages;
- a failed page.

Assertions: the adapter never reports complete coverage from count equality; duplicates
are detected; a failed page is never silently skipped. The exact coverage outcome
follows the gate 4 decision.

## 5. Existing suites stay green

The Product regression and acceptance suites
([`../../PRODUCT_REGRESSION_ACCEPTANCE.md`](../../PRODUCT_REGRESSION_ACCEPTANCE.md)) and
the OpenAPI contract test must keep passing unchanged; Integration 001 changes no
Product route.
