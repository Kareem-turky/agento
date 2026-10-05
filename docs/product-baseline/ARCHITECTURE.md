# Where FulFly would fit in the existing architecture

> **Status:** "Existing seams" is `VERIFIED_CURRENT_PRODUCT`. "FulFly placement" is
> `OPEN_ARCHITECTURE_DECISION`. This document proposes no new component; it places a
> future adapter inside seams that already exist.

## Existing seams

```text
Product API (FastAPI, 59 operations)          Agno AgentOS runtime (not Product API)
   │
   ├─ governance: trusted actor → GovernanceGate → policy → ExecutionCoordinator → audit
   │
   ├─ Operations Agent / Employee Chat ─┐
   ├─ operations.daily_report Workflow ─┼─► CommerceIntegration (read-only business seam)
   ├─ order / shipment read actions ────┘        ▲
   │                                             │ built by composition from
   │                                   APP_BUSINESS_BACKEND → BusinessBackendRegistry
   │                                   (allowlist: "mock"; inputs: BusinessBackendInputs)
   │
   ├─ operations.ticket.create ─► ticketing contract (internal governed write)
   │
   └─ Integration management (connection lifecycle only)
        IntegrationCatalog (empty in production)
          (IntegrationDefinition, IntegrationConnectionDriver: validate_config,
           test_connection, aclose)
        IntegrationConnection (metadata in PostgreSQL)
        IntegrationSecretStore (credential values, filesystem store)

Outbound HTTP for reviewed adapters: app/integrations/http/
  (IntegrationHttpTransport, IntegrationHttpPolicy) — not wired to any provider.
```

Data stores: PostgreSQL (Product schema and Agno schema). The local Compose file also
runs Redis, but readiness does not depend on it and no Integration 001 behaviour may
require it. No object storage is used.

## FulFly placement

Two separate pieces, kept apart as the canonical
[`../INTEGRATIONS.md`](../INTEGRATIONS.md) requires:

| Piece | Implements | Responsibility |
|---|---|---|
| FulFly **connection driver** | `IntegrationConnectionDriver` + an `IntegrationDefinition` (category `commerce`, auth mode `credentials`) | `validate_config` (base URL, currency id, account role), `test_connection` (login + a minimal authenticated read), `aclose`. No business operations. |
| FulFly **commerce adapter** | `CommerceIntegration` | Business reads mapped to canonical models; provider ids only in `ExternalReference`; uses `app/integrations/http/`; passes the conformance harness (`tests/commerce_conformance/`). |

How the adapter is selected and receives credentials is open (gate 5): today the
business backend comes from `APP_BUSINESS_BACKEND` with startup inputs, and integration
connections do not change it. Either a backend registration with declared input names,
or a reviewed link from a connection to the backend, must be chosen. This PR chooses
neither.

## Rules that a FulFly adapter must follow (`VERIFIED_CURRENT_PRODUCT` constraints)

- No FulFly names, statuses or rules in Core, Workflow, Agent or route code.
- No fabricated entities: no `Shipment` from order statuses, no zero-filled money, no
  default timestamps.
- Unmappable data raises `IntegrationDataError`; unreachable provider raises
  `IntegrationUnavailableError`.
- Agents, Workflows and models never receive the transport, credentials or JWT.
- Readiness (`/health/ready`) never depends on FulFly.

## Shipment and pagination blockers

The adapter cannot satisfy `list_shipments`/`get_shipment`, and cannot prove order-list
completeness. Both are documented in
[workflows/DAILY_OPERATIONS_ANALYSIS.md](workflows/DAILY_OPERATIONS_ANALYSIS.md) and are
gates 3 and 4.
