# FulFly Integration 001 — supplemental reconciliation package

This folder is a **supplemental Integration 001 reconciliation package**. It is not a
replacement for the canonical Product documentation and it is not a frozen baseline. It
records what FulFly's published API documentation says, how that maps onto the Agento
Product Core **as it exists today**, which decisions are still open, and what must be
true before any FulFly code is written.

**The current Agento Product Core is authoritative.** Where this package and the code or
the canonical docs disagree, the code and the canonical docs win, and this package is
wrong.

Canonical documentation (not modified by this package):

| Topic | Canonical document |
|---|---|
| Product API (59 operations, generated) | [`../API_REFERENCE.md`](../API_REFERENCE.md), [`../openapi/agento-product-api-v1.json`](../openapi/agento-product-api-v1.json) |
| Integration management | [`../INTEGRATIONS.md`](../INTEGRATIONS.md) |
| Product Agents (Operations Agent manifest) | [`../AGENTS.md`](../AGENTS.md) |
| Workflows (`operations.daily_report`) | [`../WORKFLOWS.md`](../WORKFLOWS.md) |
| Employee Chat (ticket proposals) | [`../EMPLOYEE_CHAT.md`](../EMPLOYEE_CHAT.md) |
| Regression acceptance | [`../PRODUCT_REGRESSION_ACCEPTANCE.md`](../PRODUCT_REGRESSION_ACCEPTANCE.md) |
| Production operations | [`../PRODUCTION_OPERATIONS.md`](../PRODUCTION_OPERATIONS.md) |

## Status vocabulary

Every statement in this package carries, or sits under a heading that carries, one of
these labels. A document's header label applies to its content unless a section or line
says otherwise.

| Label | Meaning |
|---|---|
| `VERIFIED_CURRENT_PRODUCT` | Checked against the current repository code or canonical docs. |
| `VERIFIED_PROVIDER_DOC` | Stated by FulFly's published API documentation. Not checked against a live account. |
| `APPROVED_INTEGRATION_DECISION` | A decision for Integration 001 that this package records as settled. |
| `OPEN_ARCHITECTURE_DECISION` | A decision that must be made (and reviewed) before implementation. |
| `PROPOSED_FUTURE` | A possible future change. Nothing here is implemented, approved or scheduled. |
| `REQUIRES_LIVE_VALIDATION` | Cannot be settled from documentation; needs a controlled check against a real FulFly account. |

## Index

| Document | Content | Dominant status |
|---|---|---|
| [INTEGRATION_001_DECISIONS.md](INTEGRATION_001_DECISIONS.md) | Capability decision matrix and the eight-decision implementation gate | Mixed; gate is `OPEN_ARCHITECTURE_DECISION` |
| [integrations/FULFLY_CONTRACT.md](integrations/FULFLY_CONTRACT.md) | Provider discovery from FulFly's published docs | `VERIFIED_PROVIDER_DOC` |
| [COMMERCE_DOMAIN.md](COMMERCE_DOMAIN.md) | FulFly field → existing Core model mapping analysis | Mixed; mapping is incomplete |
| [PROJECT_OVERVIEW.md](PROJECT_OVERVIEW.md) | What Integration 001 is and is not | Mixed |
| [ARCHITECTURE.md](ARCHITECTURE.md) | Where a FulFly adapter would sit in the existing seams | `VERIFIED_CURRENT_PRODUCT` + `OPEN_ARCHITECTURE_DECISION` |
| [workflows/DAILY_OPERATIONS_ANALYSIS.md](workflows/DAILY_OPERATIONS_ANALYSIS.md) | The existing daily workflow vs FulFly capabilities | Mixed |
| [OPERATIONS_AGENT.md](OPERATIONS_AGENT.md) | The existing Operations Agent and Integration 001 effects | `VERIFIED_CURRENT_PRODUCT` |
| [TOOLS_CATALOG.md](TOOLS_CATALOG.md) | Current tools vs adapter-internal provider operations | Mixed |
| [POLICY_AND_PERMISSIONS.md](POLICY_AND_PERMISSIONS.md) | Current actions and permissions | `VERIFIED_CURRENT_PRODUCT` |
| [INTEGRATION_001_API_IMPACT.md](INTEGRATION_001_API_IMPACT.md) | Product API impact (none in this PR) and future proposals | `VERIFIED_CURRENT_PRODUCT` + `PROPOSED_FUTURE` |
| [CONFIGURATION.md](CONFIGURATION.md) | Connection config and secret placement | Mixed |
| [SECURITY.md](SECURITY.md) | FulFly-specific security requirements | Mixed |
| [GETTING_STARTED.md](GETTING_STARTED.md) | How to use this package | `VERIFIED_CURRENT_PRODUCT` |
| [deployment/LOCAL.md](deployment/LOCAL.md) | Local development facts relevant to Integration 001 | `VERIFIED_CURRENT_PRODUCT` |
| [deployment/PRODUCTION.md](deployment/PRODUCTION.md) | Deployment facts and Integration 001 additions | Mixed |
| [testing/TEST_STRATEGY.md](testing/TEST_STRATEGY.md) | Test requirements for a future adapter | `PROPOSED_FUTURE` |
| [runbooks/TROUBLESHOOTING.md](runbooks/TROUBLESHOOTING.md) | Diagnostic notes for a future adapter | `PROPOSED_FUTURE` |

## Scope of this package

- FulFly Integration 001 introduces no external/provider write capability.
- Existing Agento internal/governed ticket and approval capabilities remain unchanged.
- This documentation PR adds or changes no Product route, permission, tool, Agent,
  Workflow, migration, dependency or runtime behaviour.
- No FulFly code exists, and no FulFly code may be written until every item of the
  [implementation gate](INTEGRATION_001_DECISIONS.md#implementation-gate) is closed.

## Evidence limits

FulFly provider facts come from FulFly's published API reference as reviewed on
2026-10-05. They were not re-fetched during this reconciliation (the provider site was
not reachable from the review environment), so this reconciliation only **narrows** or
qualifies provider claims; it adds no new provider facts. No live FulFly account was used.
