# Documentation Index

| Document | Purpose | Status |
|---|---|---|
| [Project Overview](PROJECT_OVERVIEW.md) | Product scope, decisions, MVP, and boundaries | Baseline complete |
| [Architecture](ARCHITECTURE.md) | Components, dependency rules, and runtime flow | Baseline complete |
| [Getting Started](GETTING_STARTED.md) | Prerequisites and implementation onboarding | Requires repository reconciliation |
| [Configuration](CONFIGURATION.md) | Configuration ownership and proposed schema | Contract baseline |
| [Commerce Domain](COMMERCE_DOMAIN.md) | Entities, provider mapping, and invariants | MVP baseline |
| [Operations Agent](OPERATIONS_AGENT.md) | Agent purpose, inputs, outputs, and restrictions | MVP baseline |
| [Tools Catalog](TOOLS_CATALOG.md) | Tool contracts, risk, and verification | MVP baseline |
| [Policy and Permissions](POLICY_AND_PERMISSIONS.md) | Authorization, policies, risks, and approvals | Baseline complete |
| [Security](SECURITY.md) | Trust boundaries and security requirements | Baseline complete |
| [API Reference](API_REFERENCE.md) | Planned Agento HTTP API boundary | Proposed; routes unverified |
| [FulFly Contract](integrations/FULFLY_CONTRACT.md) | Verified FulFly API discovery and mapping | Discovery complete |
| [Daily Operations Workflow](workflows/DAILY_OPERATIONS_ANALYSIS.md) | Deterministic pipeline and report contract | MVP baseline |
| [Local Deployment](deployment/LOCAL.md) | Local development expectations | Requires repository reconciliation |
| [Production Deployment](deployment/PRODUCTION.md) | Isolated deployment and release controls | Baseline complete |
| [Test Strategy](testing/TEST_STRATEGY.md) | Required test layers and MVP acceptance criteria | Baseline complete |
| [Troubleshooting](runbooks/TROUBLESHOOTING.md) | Failure diagnosis and safe recovery | Baseline complete |

## Documentation status vocabulary

- **Verified:** Confirmed against a running system or source code.
- **Discovery complete:** Confirmed against an external provider's published contract.
- **Baseline:** Approved design that implementation must satisfy.
- **Proposed:** Recommended interface that is not implemented or verified yet.
- **Requires repository reconciliation:** The implementation exists, but this supplemental document has not yet been verified against its exact commands and contracts.

When code is introduced, every pull request that changes behaviour must update the relevant document and change proposed statements to verified statements only after tests confirm them.
