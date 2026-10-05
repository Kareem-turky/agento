# Integration 001 — Product API impact

The existing Product API is authoritative. Integration 001 does not add or change any Product route in this documentation PR.

> **Status:** "Current API" is `VERIFIED_CURRENT_PRODUCT`. "Future proposals" are
> `PROPOSED_FUTURE`: not implemented, not approved, not scheduled.

## Current API (authoritative)

The Product API has 59 operations, documented and generated in:

- [`../API_REFERENCE.md`](../API_REFERENCE.md) — human reference (conventions,
  errors, boundaries, generated endpoint reference);
- [`../openapi/agento-product-api-v1.json`](../openapi/agento-product-api-v1.json) —
  the OpenAPI contract.

This package does not duplicate them. Conventions that any future FulFly-related route
must follow are defined there, notably: fixed paths with identifiers in query
parameters or the body (no path parameters), Product API key authentication, the
`SafeValidationError` / `ErrorDetail` / `CodedErrorDetail` error bodies, and
`Idempotency-Key` for governed writes.

Routes relevant to Integration 001 that **already exist**:

| Route | Relevance |
|---|---|
| `GET /api/v1/operations/reports/daily` | The deterministic daily report. With FulFly it is blocked by the shipment and pagination gates. |
| `POST /api/v1/operations/runs` | Read-only Operations Agent run over the selected business backend. |
| `GET /api/v1/integrations/catalog`, `.../connections`, `.../connection`, `.../connection/credentials`, `.../connection/test`, `.../connection/enable`, `.../connection/disable` | Generic connection lifecycle. A FulFly connection would use these unchanged once a reviewed `IntegrationDefinition` and driver are installed. |
| `GET /health/live`, `GET /health/ready` | Readiness never depends on an integration. |

## What Integration 001 changes in the API

Nothing. No route, schema, permission, error body or status code is added or changed.
If a later reviewed task needs an API change, it updates the canonical
[`../API_REFERENCE.md`](../API_REFERENCE.md) and the OpenAPI artifact, which are
guarded by `tests/api/test_product_openapi_contract.py`.

## Future proposals (`PROPOSED_FUTURE`)

Earlier drafts of this package listed the routes below as an "MVP" API. They are **not
implemented** and are kept only so reviewers can see what was considered and why each
is not needed now.

| Earlier proposal | Why it is not part of Integration 001 |
|---|---|
| `POST /api/v1/operations/reports` (start a report run) | The daily report already exists as `GET /api/v1/operations/reports/daily`. Any change would follow the fixed-path convention. |
| `GET /api/v1/operations/runs/{run_id}` | Path parameters are not used by the Product API. Durable Workflow runs are already readable at `GET /api/v1/workflows/run?run_id=`. |
| `GET /api/v1/operations/reports/{report_id}` | Path parameters are not used. Reports are not persisted, so there is no report id. Report persistence would be a separate Core decision. |
| `GET /api/v1/integrations/fulfly/health` | Provider-specific routes are not used. Connection health is the generic `POST /api/v1/integrations/connection/test?connection_id=`. |
| `POST /api/v1/webhooks/fulfly/order-status` | No webhook or ingest route exists, and the FulFly webhook is unsigned. Ingestion would be a separate reviewed task (see [SECURITY.md](SECURITY.md#webhook-discovery-notes)). |

Earlier drafts also proposed permissions `operations.report.run`,
`operations.report.read` and `integrations.health.read`. None exists; see
[POLICY_AND_PERMISSIONS.md](POLICY_AND_PERMISSIONS.md).
