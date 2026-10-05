# Getting started with this package

> **Status:** `VERIFIED_CURRENT_PRODUCT` (navigation only).

This package is documentation only. It does not change how the Product is installed or
run; use the repository [`README.md`](../../README.md) for that (`./scripts/demo.sh up`
runs the demo against the deterministic `mock` backend).

Suggested reading order for a reviewer:

1. [README.md](README.md) — scope, status vocabulary, canonical docs.
2. [INTEGRATION_001_DECISIONS.md](INTEGRATION_001_DECISIONS.md) — capability matrix and
   the implementation gate.
3. [integrations/FULFLY_CONTRACT.md](integrations/FULFLY_CONTRACT.md) — what FulFly
   documents.
4. [COMMERCE_DOMAIN.md](COMMERCE_DOMAIN.md) — field-by-field mapping onto the existing
   Core models.
5. [workflows/DAILY_OPERATIONS_ANALYSIS.md](workflows/DAILY_OPERATIONS_ANALYSIS.md) —
   shipment and pagination blockers.
6. The remaining documents as needed.

Before any FulFly code is written, every gate item must be closed and recorded as
`APPROVED_INTEGRATION_DECISION`. Live FulFly checks are manual and opt-in; never use
production credentials in a developer checkout or in CI.
