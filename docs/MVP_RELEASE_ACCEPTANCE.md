# MVP release acceptance (v1)

**Classification: MVP technical release candidate (MVP acceptance baseline).**

This document records what the MVP acceptance gate certifies, how to run it, and what is
still missing. It does not approve any production use: see
[Production business use is still blocked](#production-business-use-is-still-blocked).
No version, tag, release or image publication is associated with it.

## What the gate is

Two automated gates, both required on every pull request and on every push to `main`
(`.github/workflows/ci.yml`):

| Gate | Where | Proves |
|---|---|---|
| **MVP business acceptance** | Backend (Python) job, step "MVP business acceptance (mock/test only, no external calls)": `tests/acceptance/test_mvp_operations_e2e.py` | The first MVP use case end to end through Product HTTP |
| **Packaged installation** | Infrastructure job, `.github/scripts/deployment-smoke.sh` | The API and Web images and the four-service `deployments/template` (console, BFF, Product auth, private API and AgentOS, migrations, non-root, read-only root filesystem, clean shutdown, production fail-closed) |

The frontend job (typecheck, build, session-context tests, BFF proxy smoke) and the full
backend suite (unit and PostgreSQL integration tests, lint, format) remain required.

### How the business acceptance runs

- `APP_ENVIRONMENT=test`, `APP_BUSINESS_BACKEND=mock`, composed by the real
  `app.bootstrap.create_deployment_app`, with real Product API-key authentication and a
  real, migrated PostgreSQL (the only dependency).
- The deterministic mock commerce and ticketing fixture, unchanged, with the canonical
  store and business date `2026-03-03`.
- The Operations Agent runs on the TEST-ONLY `ScriptedToolModel`
  (`tests/support/scripted_tool_model.py`), passed through the existing `model=` test
  seam. It is not a runtime model provider and is never part of production composition.
- Every business result is obtained through Product HTTP (FastAPI `TestClient`). The
  database is inspected only to prove durability and audit.
- No LLM, model provider, external HTTP or MCP call is made. A guard fails the test on
  any Python-level outbound connection.
- Test keys are obviously test-only. Request credentials are redacted in pytest failure
  output.

## Business scenario (certified, steps 1–10)

An operator of one company reviews the deterministic operations snapshot of one store on
the canonical acceptance business date (`2026-03-03`) and follows up on a problem:

1. The Product answers `GET /health` without credentials.
2. The operator requests the daily operations report for the store and `2026-03-03`
   (`GET /api/v1/operations/reports/daily`). It returns 1 order created, 1 shipment
   shipped, 1 failed shipment, exactly one critical shipment finding, and inventory
   `not_included`, with no provider identifiers, tracking, courier or customer data.
3. The operator asks the Operations Agent "Analyze operations for 2026-03-03."
   (`POST /api/v1/operations/runs`). The Agent calls `get_daily_operations_report` and
   answers from it.
4. The Agent's report is identical to the direct report (apart from `generated_at`),
   produced by the same deterministic workflow. No provider or private data reaches the
   answer or the model.
5. Analysis is read-only: no write command, no audit event and no ticket is created.
6. When the Agent tries to create a ticket during analysis, the Product denies it
   (`status=denied`, `reason=action_not_requested`): no ticket, no command, no audit.
7. The operator explicitly creates an operational ticket with an `Idempotency-Key`
   (`POST /api/v1/operations/tickets`). The response is `201` with status `verified`,
   `replayed=false`, a `ticket_id`, a `command_id` and `persistence_complete=true`. The
   command and its complete audit lifecycle (`requested`, `policy_decided`,
   `execution_started`, `execution_completed`, `verification_started`, `verified`) are
   in PostgreSQL.
8. Retrying with the same key is a replay (`200`, `replayed=true`, same `command_id`
   and `ticket_id`). No second ticket, command or audit lifecycle is created.
9. The operator reads the command status
   (`GET /api/v1/operations/tickets/commands`). After a restart (a new application on the
   same database), the status is still returned, and a replay still returns the same ids
   without executing anything again.
10. Security fails closed:
    - Protected routes return `401` without a key, with a wrong key, or with the AgentOS
      key. The AgentOS routes reject the Product key.
    - A store the key is not granted returns `403`.
    - Invalid input returns the existing validation status (`422`, or `400` for a
      missing `Idempotency-Key`), and nothing reaches the model or the database.
    - Another principal's command is indistinguishable from a nonexistent one (`404`).

## Certified (MVP acceptance baseline)

- The Product HTTP surface (5 routes: `/health`, runs, daily report, ticket create,
  ticket command status) behaves as described above against the mock backend.
- The Operations Agent (4 tools, `tool_call_limit` 6) is read-only on the analysis
  surface. Writes only happen through the explicit, governed, idempotent ticket command.
- Durable write commands and the audit lifecycle in PostgreSQL, idempotent replay, and
  status survival across restarts.
- Product API-key authentication, store scoping and command privacy.
- The packaged installation (API and Web images, four-service Compose template), as
  proven by the deployment smoke.
- The Operations Console builds, and its BFF forwards only fixed Product routes: the five
  above plus the integration- and Agent-management routes below.

## Integration foundation (framework only)

A provider-agnostic integration foundation exists (see
[`docs/INTEGRATIONS.md`](INTEGRATIONS.md)):

- a static integration catalog;
- connection metadata in PostgreSQL (migration `0003`);
- a filesystem secret store (`APP_INTEGRATION_SECRETS_DIR`), whose protection depends on
  volume security (no KMS/HSM);
- a governed, audited Connections API (`/api/v1/integrations/*`);
- the `/settings/integrations` UI.

It is proven with deterministic fake definitions and drivers only. **No real provider is
connected or installed**: the production catalog is empty. Business data is not
ingested or mirrored, and source systems remain the source of truth. OAuth is not
implemented. The demo still uses mock data. This foundation does **not** lift the
production blocker below.

## Product Agent management (lifecycle only)

Product Agents are described by an immutable, explicit `ProductAgentCatalog`, which
contains exactly the Operations Agent (see [`docs/AGENTS.md`](AGENTS.md)). Operators with
`agents.manage` can enable or disable an Agent. Changes are governed, audited and stored in
`product.agent_configurations` (migration `0004`).

The Operations Agent defaults to enabled, so the demo and fresh installations behave as
before without setup. When it is disabled, `POST /api/v1/operations/runs` answers `409`
before any model or tool call. The deterministic daily report and the explicit ticket
write do not depend on it.

There is no prompt editing, no Agent creation and no dynamic code loading, and this does
not lift the production blocker below.

## Product Skills and Tasks (metadata only)

The Operations Agent's capabilities are described by three Skills and three Tasks with
explicit acceptance criteria. These are immutable, read-only Product metadata validated at
startup (see [`docs/SKILLS_AND_TASKS.md`](SKILLS_AND_TASKS.md)).

- They change no runtime behaviour and grant nothing.
- They add no persistence, and no Task executor exists yet.

## Production business use is still blocked

**PRODUCTION BUSINESS USE IS STILL BLOCKED.**

- No authoritative API contract of a real business system has been supplied.
- No reviewed real business-backend adapter is registered: the backend registry contains
  only `mock`.
- With `APP_ENVIRONMENT=staging` or `production`, the API therefore refuses to start
  (`disabled` fails closed, and `mock` is refused).

This is the intended, fail-closed state, not a defect. It ends only when a real contract
is supplied and a reviewed adapter for it passes the commerce integration conformance
harness.

## Local runnable demo is available

**Production business use is blocked because there is no real backend.** Separately, a
**local runnable demo is fully available using deterministic internal demo data**:
`./scripts/demo.sh up` (see [`deployments/demo/README.md`](../deployments/demo/README.md)).

- The demo runs the same Product images, services, authentication, governance, workflow,
  Agent, durable commands and audit.
- It uses the existing `mock` commerce backend and a LOCAL-DEMO-ONLY deterministic model
  (`APP_DEFAULT_MODEL_PROVIDER=demo`).
- It is pinned to `APP_ENVIRONMENT=local`. Both the mock backend and the demo model are
  refused in staging and production.
- It is **not** a production integration and does not change the blocker above.
- CI exercises it through the Web port (`.github/scripts/demo-smoke.sh`, Infrastructure
  job, after the deployment smoke).

## Not blocking this baseline (future work)

**Outside the MVP scope:**

- a real business-backend adapter (requires the contract above);
- more business workflows, actions or reports (for example inventory in the daily
  report);
- login, user accounts and server-side sessions (the Product API key is entered per
  browser tab);
- approval workflows beyond the baseline governance policy.

**Required before any production deployment:**

- the real business backend described above;
- TLS termination, a reverse proxy and a reviewed public ingress design (AgentOS must
  never be directly exposed to the internet);
- backup and restore of the PostgreSQL volume;
- image registry publication and release automation;
- operational monitoring and alerting;
- a production model-provider configuration and its review, if the Agent is to use a
  live model.

## Running the acceptance

With PostgreSQL running and migrated (root README, sections 7 and 10):

```bash
set -a; . ./.env; set +a
uv run alembic upgrade head
REQUIRE_INTEGRATION_TESTS=1 uv run pytest tests/acceptance/test_mvp_operations_e2e.py -q
```

Without `APP_DATABASE_URL` the acceptance tests skip locally. With
`REQUIRE_INTEGRATION_TESTS=1` (as in CI), a missing database is a failure.

Packaged installation (Docker required):

```bash
docker build -f apps/api/Dockerfile -t commerce-ai-platform-api:local .
docker build -f apps/web/Dockerfile -t commerce-ai-platform-web:local .
.github/scripts/deployment-smoke.sh commerce-ai-platform-api:local commerce-ai-platform-web:local
```

CI results for a given commit are shown by the three required jobs of the CI workflow
(Backend (Python), Frontend (Next.js), Infrastructure (Docker Compose)).
