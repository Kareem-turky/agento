# FulFly Integration 001 — overview

> **Status:** mixed; labels per section. This is an overview of the Integration 001
> reconciliation, not a description of the whole Product. For the Product itself use
> the repository [`README.md`](../../README.md) and the canonical docs listed in
> [README.md](README.md).

## 1. What Integration 001 is (`APPROVED_INTEGRATION_DECISION`)

The first candidate real commerce provider for Agento: FulFly, connected through the
**existing** Product seams with **read-only** provider access. Its purpose is to let the
existing Operations capabilities (order reads, the daily report, the Operations Agent,
Employee Chat) work over real FulFly data where, and only where, FulFly's API can
satisfy the existing Core contracts honestly.

It is **not**:

- a new architecture, runtime, Agent, Workflow, tool, permission, route or data store;
- a FulFly-shaped Core (no FulFly names, statuses or rules in Core code);
- a provider write integration: FulFly Integration 001 introduces no external/provider
  write capability;
- a change to existing internal writes: existing Agento internal/governed ticket and
  approval capabilities remain unchanged.

## 2. Current Product facts that bound Integration 001 (`VERIFIED_CURRENT_PRODUCT`)

- **Product API:** 59 operations, fixed paths, documented in
  [`../API_REFERENCE.md`](../API_REFERENCE.md). Integration 001 changes none.
- **Business seam:** `CommerceIntegration` (`get_store`, `get_order`, `list_orders`,
  `get_shipment`, `list_shipments`, `get_inventory`; `OrderQuery`, `ShipmentQuery`),
  read-only, returning canonical models only.
- **Canonical domain:** `Order`, `OrderItem`, `OrderStatus`, `Shipment`,
  `ShipmentStatus`, `InventoryLevel`, `Warehouse`, `Product`, `Variant`, `Store`, with
  Product-owned UUID ids and provider ids only in `ExternalReference`.
- **Business backend selection:** `APP_BUSINESS_BACKEND` → an explicit
  `BusinessBackendRegistry`; the only registration is `mock`. Staging and production
  refuse to start because no real backend exists.
- **Integration management:** `IntegrationDefinition`, `IntegrationCatalog` (empty in
  production), `IntegrationConnection`, `IntegrationConnectionDriver`
  (`validate_config`, `test_connection`, `aclose`), `IntegrationSecretStore`. It manages
  connection lifecycle only and does not change the business backend.
- **Outbound HTTP:** `app/integrations/http/` provides a secure transport not yet wired
  to any provider.
- **Operations Agent:** exists, with three governed read tools and one internal ticket
  write tool (refused in read-only runs). Employee Chat can only propose a ticket.
- **Daily operations workflow:** deterministic, fails closed, requires store, order
  **and shipment** reads, has fixed coverage values and no partial state, persists no
  report.
- **Approvals:** a durable approval foundation exists; no real action needs one today.

## 3. What FulFly offers (`VERIFIED_PROVIDER_DOC`)

See [integrations/FULFLY_CONTRACT.md](integrations/FULFLY_CONTRACT.md). In short:
credential login to a 12-hour JWT with a mandatory `currency` header; page-based order
list (Affiliate only) with no filters or snapshot; order detail and status history;
shipping reference data; partially documented variant reads; an unsigned status
webhook. No shipment, warehouse, return or settlement API.

## 4. Key mismatches (`OPEN_ARCHITECTURE_DECISION`)

| Mismatch | Effect | Where |
|---|---|---|
| No shipment API, but the daily workflow requires shipments | FulFly is not a drop-in backend; shipments are never fabricated | [workflows/DAILY_OPERATIONS_ANALYSIS.md](workflows/DAILY_OPERATIONS_ANALYSIS.md#capability-mismatch-shipments) |
| Mutable page-based order list | Completeness cannot be proven; coverage is `unverified`/`partial` | same, [Pagination coverage](workflows/DAILY_OPERATIONS_ANALYSIS.md#pagination-coverage-blocker) |
| List summaries have no items; `Order.items` needs ≥ 1 | Detail read per order; item schema unproven | [COMMERCE_DOMAIN.md](COMMERCE_DOMAIN.md) |
| No store profile, timezone or Product ids from FulFly | Store anchoring and ID strategy needed | [COMMERCE_DOMAIN.md](COMMERCE_DOMAIN.md#store) |
| Several FulFly statuses have no faithful `OrderStatus` | Map to `unknown` or decide in Core | [COMMERCE_DOMAIN.md](COMMERCE_DOMAIN.md#status-mapping) |
| Credentials: business backend inputs vs connection secrets | Credential source must be chosen | [CONFIGURATION.md](CONFIGURATION.md) |
| Conformance harness assumes a full-capability adapter (all read capabilities, shipment and inventory fixtures) | A partial-capability provider cannot pass it as written; a generic capability-aware model is needed first | [INTEGRATION_001_DECISIONS.md](INTEGRATION_001_DECISIONS.md#implementation-gate) (gate 8) |

## 5. Identity (`OPEN_ARCHITECTURE_DECISION`, gate 2)

Canonical ids are Product-owned UUIDs; FulFly ids (24-hex `_id`) live only in
`ExternalReference("fulfly", _id)`. The mock adapter derives ids with UUID5 from a
fixed Product namespace and `"<entity type>:<provider key>"`. For FulFly the options
are:

1. a persistent identity mapping (provider id → Product UUID), which is new Core
   persistence and needs its own reviewed decision; or
2. a reviewed deterministic Product-owned UUID scheme (UUID5 with a dedicated,
   never-changing namespace, scoped by entity type, and by connection/account if one
   installation could see more than one FulFly account).

Requirements either way: the same FulFly entity always maps to the same UUID; no
collisions across entity types; the provider id is never the canonical identity.

## 6. Decisions and gate

The capability matrix and the eight-decision implementation gate are in
[INTEGRATION_001_DECISIONS.md](INTEGRATION_001_DECISIONS.md). No FulFly code may be
written until the gate is closed.

## 7. Out of scope (`APPROVED_INTEGRATION_DECISION`)

- Any FulFly write (orders, cancellation, products, images, XLSX export).
- Webhook ingestion (discovery notes only; see [SECURITY.md](SECURITY.md#webhook-discovery-notes)).
- New report persistence, report routes, metrics or thresholds.
- New permissions, tools, Agents or Workflows specific to FulFly.
- Inventory in the daily report (unchanged: `not_included`).
