# Product regression acceptance (current gate)

Task 041 turned the Task 040 Product Core acceptance suite into a continuously-run,
**evolvable** regression gate. The Product Core release itself is preserved, exactly and
unchanged, as historical evidence:
[`PRODUCT_CORE_RELEASE_BASELINE.md`](PRODUCT_CORE_RELEASE_BASELINE.md) (commit, tree,
digest, file count, catalogs at release), together with the release decision
[`PRODUCT_CORE_READY.md`](PRODUCT_CORE_READY.md) and the acceptance record
[`PRODUCT_CORE_ACCEPTANCE.md`](PRODUCT_CORE_ACCEPTANCE.md).

## The CI gate

The CI gate is the Backend job step **"Product regression acceptance"**. It runs
`uv run pytest tests/acceptance -q` on the migrated PostgreSQL with
`REQUIRE_INTEGRATION_TESTS=1`, so a missing database fails the step and never skips it.
It runs after the migrations and the full suite.

- **Why the step was renamed.** At the release the same step was named "Product Core
  acceptance". It now guards every later change to `main`, not only the release, and the
  old name would read as if `main` still had to equal the release. The historical
  documents keep the old name, because that was the name of the release gate.
- **What the suite contains.** The MVP acceptance (Task 029) plus the
  `test_product_core_*` journeys of Task 040.

## How the assertions are classified

| Kind | Where it lives now | Examples |
|---|---|---|
| **Historical release snapshot** | [`PRODUCT_CORE_RELEASE_BASELINE.md`](PRODUCT_CORE_RELEASE_BASELINE.md) and the unchanged release documents. A test checks that the record keeps the exact values, and that the release documents keep their exact content | Whole-production digest `be4fc703…` and 335 files. Migrations exactly `0001`–`0008` with head `0008`. Exact catalogs. Integration catalog and messaging registry empty. Backends exactly `{mock}`. No production MEDIUM/HIGH action. Provider-free. Browser suite local-only. "Next phase: Real Integrations" |
| **Enduring regression invariant** | Actively tested on every change (`tests/acceptance`) | The rest of this page |

A historical snapshot is **not deleted**: it is recorded exactly. It simply no longer
asserts that the evolving `main` must equal it.

## The enduring invariants (actively tested)

- **Security boundaries**
  - Product and AgentOS credentials stay separate, and neither authenticates the other's
    routes.
  - Store isolation and company repository isolation hold. Unknown and foreign IDs fail
    closed.
  - No request-validation answer, Operations included, echoes a submitted value, key,
    `input` or `ctx`. Invalid requests have no side effect.
  - Model output cannot set actor, company, store or permissions, and cannot bypass
    Policy or Governance. The Operations read path stays read-only.
  - External and tool text (Knowledge, Conversations) stays untrusted, inert data.
  - Credentials never appear in API responses, logs, the audit trail or database metadata.
- **Product behaviour**
  - Disabling an Agent stops execution before the model is called. Agent state is durable.
  - WriteCommand idempotency, verification and audit work, including replay after a
    restart.
  - Approvals: the two-person rule, exact requester binding and one-time continuation.
  - Workflows: the Product-owned deterministic Workflow, the approval wait, and restart
    durability.
  - Conversation ingress trusts only the Product `ChannelContext`.
  - Observability is bounded and carries no business text.
- **Architecture**
  - Production code imports no tests, fakes or acceptance harness.
  - There is no acceptance switch or backdoor.
  - There is no generic injection, ingest, webhook, send or execute route, and no Approval
    create route.
  - No TEST-ONLY action, Workflow or integration definition exists in production.
- **Migrations (extensible)**
  - Revisions `0001`–`0008` still exist, byte-identical to the release.
  - The chain has exactly one head, and that head descends from `0008` through a single
    linear chain back to `0001`.
  - Every release table still exists.
  - Later reviewed tasks may add revisions after `0008`.
- **Catalogs (extensible)**
  - The `operations` Agent, its three Skills, its three Tasks and the
    `operations.daily_report` Workflow still exist.
  - `operations.ticket.create` stays `LOW_RISK_WRITE` until a reviewed task changes it.
  - The `mock` backend remains, because local, test and acceptance installations use it.
  - Additional Agents, Skills, Tasks, Workflows, integrations and backends are allowed.
- **Provider boundary**
  - Concrete adapter packages (`app/integrations/<category>/<adapter>/`) are imported
    only by the composition root (`app/composition`) and by themselves.
  - Contract packages never load an adapter.
  - Domain, service and route code stays provider-agnostic.
  - The Agent, model and tool layer never imports integration secret storage, so the LLM
    never receives provider credentials.
  - Real provider adapters are allowed behind that boundary. The TEST-ONLY generic fakes
    of the acceptance harness stay generic.
- **Dependencies:** Agno stays an exact pin (`agno[...]==<version>`), never a floating
  range.

Changing an enduring invariant needs its own reviewed task, with the change stated
explicitly. A future feature must never weaken one silently in order to pass.
