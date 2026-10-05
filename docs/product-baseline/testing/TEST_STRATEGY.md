# Test strategy for a future FulFly adapter

> **Status:** `PROPOSED_FUTURE`. Existing test infrastructure named here is
> `VERIFIED_CURRENT_PRODUCT`. No FulFly test exists, and none is added by this PR.

## Rules

- **No live calls in CI.** All FulFly tests use synthetic fixtures with no production
  PII or credentials. Live checks are opt-in, manual, and never part of the CI gate.
- **The Core is the oracle.** Tests assert canonical outputs (`Order`, `OrderItem`,
  `Money`, `ExternalReference`, contract errors), not provider shapes.
- **No fabrication.** Tests prove the adapter refuses rather than invents.

## 1. Core conformance (existing harness)

The adapter must pass the existing `CommerceIntegration` conformance harness
(`tests/commerce_conformance/`), as the `mock` adapter does
(`tests/integrations/test_mock_conformance.py`). Where FulFly cannot provide a
capability (shipments), the expected conformance behaviour depends on gate 3 and must
be decided before the adapter is written.

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
- Whatever gate 3 decides (Option A, Option B or unsupported) is tested end to end
  through the existing daily report route and the Operations Agent tools.

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
