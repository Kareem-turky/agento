# commerce-ai-platform

> Temporary name. Repository: `agento`.

## 1. What this is

A general-purpose, installable **AI operating layer for commerce and business operations**.
It is designed to be deployed by any business and connected to arbitrary external business
systems through API contracts and adapters.

This repository currently contains the **technical foundation only**: no business features,
no production agents, no integrations.

Core principles:

- **Agno is the agent runtime/framework foundation.** It is a normal, pinned, external
  dependency (`agno==3.0.11`). It is not forked, vendored or modified here.
- **The product layers around Agno are ours** — API, orchestration boundaries, policies,
  permissions, approvals, verification, audit, integration contracts, and so on.
- **No company-specific business logic belongs in the core.** Per-installation behaviour is
  supplied as configuration (`company/`) and adapters (`integrations/adapters/`).

## 2. High-level architecture

```
            ┌──────────────┐        ┌──────────────────────────────────┐
  users ──▶ │  apps/web    │ ─────▶ │  apps/api — one FastAPI process   │
            │  (Next.js)   │  HTTP  │   ├─ Product API (/health, later) │
            └──────────────┘        │   └─ Agno AgentOS runtime routes  │
                                    │        (OS_SECURITY_KEY-guarded)  │
                                    └──────┬───────────────┬────────────┘
                                           │               │
                          core/ · commerce/ · intelligence/   (future product layers)
                                           │
                          integrations/contracts ◀─ integrations/adapters ─▶ external systems
                                           │
                               PostgreSQL + pgvector      Redis
```

Implemented today: `apps/api` (Product `/health` + Agno AgentOS runtime persisted in
PostgreSQL), `apps/web` (placeholder page) and local PostgreSQL/Redis infrastructure.

### Agent runtime (Agno AgentOS)

**How FastAPI and AgentOS are integrated.** `apps/api/app/main.py` builds the Product API as
a normal FastAPI app, then `apps/api/app/runtime/agentos.py` passes it to Agno as
`AgentOS(base_app=app, on_route_conflict="preserve_base_app", ...)` and calls
`agent_os.get_app()`. AgentOS mounts its routes, middleware and lifespan onto *our* app, so
the result is a single application and a single server. Where a path exists in both (today
only `GET /health`), the Product API route is kept.

**Ownership boundary.** Agno owns the runtime: agents/teams/workflows execution, sessions,
runs, the AgentOS REST API and its tables. We own the product: configuration, the Product
API, and — in later tasks — domain logic, policies, permissions, approvals and integrations.
We use Agno's native classes (`AgentOS`, `PostgresDb`, `AgnoAPISettings`) and do not
re-implement or fork them.

**PostgreSQL schema ownership.**

```
PostgreSQL database
├── agno_runtime      Agno-owned. Created and migrated by Agno (PostgresDb) on startup:
│                     agno_sessions, agno_runs, agno_memories, agno_metrics, ...
└── (product schemas) Ours, later. Nothing is created yet.
```

The schema name comes from `APP_AGNO_DB_SCHEMA` (default `agno_runtime`). Do not write to
Agno's tables from product code.

**Registered agents** (decided in `app/runtime/components.py`):

| Agent | Registered when | Purpose |
|---|---|---|
| `runtime-smoke-test` | `APP_ENVIRONMENT` is `local` or `test` | Proves component registration. Uses `NonExecutingModel` (raises if invoked); never executed. Never registered in `staging`/`production`. |
| `generic-reasoning` | a default model is configured (any environment) | Proves the real model execution path. Not a business agent. |

With the provider `disabled` in `staging`/`production`, AgentOS starts with no agents — the
platform never needs an LLM provider just to boot.

### Model providers

**Agno is the provider abstraction.** Agents depend on Agno's `Model`
(`agno.models.base.Model`); our code never defines its own model/LLM interface.
`app/runtime/models.py` only maps configuration to a native Agno model class:

```
APP_DEFAULT_MODEL_PROVIDER + APP_DEFAULT_MODEL_ID
        │  build_default_model(settings)
        ▼
openai    → agno.models.openai.responses.OpenAIResponses(id=...)
anthropic → agno.models.anthropic.claude.Claude(id=...)
disabled  → None (no live agent)
        │
        ▼
generic-reasoning Agent(model=...) → AgentOS(agents=[...])
```

- **Supported providers:** OpenAI (Responses API) and Anthropic. Provider-specific code
  lives **only** in the model factory; business agents must depend on Agno `Model`, never on
  OpenAI/Anthropic classes.
- **Explicit model IDs:** `APP_DEFAULT_MODEL_ID` has no default and is always passed to the
  model; Agno's built-in default IDs are never relied on.
- **Credentials:** the providers' standard variables (`OPENAI_API_KEY`,
  `ANTHROPIC_API_KEY`), read by Agno's model classes. They are not product settings, are
  never stored in PostgreSQL, logged, or returned by `/health`; the factory only checks
  that the selected provider's key is present. Only that provider's key is required.
- **Fail fast:** selecting a provider without `APP_DEFAULT_MODEL_ID` or without its key
  stops startup with a `ModelConfigurationError` naming the missing variable; unsupported
  provider values are rejected by settings validation. `disabled` (the default) is valid.
- **Adding a provider** (e.g. Google, OpenRouter, a local model) means adding one entry to
  `_PROVIDERS` in the factory and its Agno extra — no agent or domain code changes.
- **Default, not only:** this is the deployment-default model for generic agents; later
  agents may use other models.

**`generic-reasoning`** (`app/agents/generic_reasoning.py`) is a plain Agno `Agent` with the
configured model, no tools, no knowledge/RAG, no memory behaviour and no system access. Its
instructions keep it to the information in the user's message (no outside facts) and forbid
claiming company data, external systems, tools or performed actions. It is infrastructure
validation, not a product chatbot.

**Security.** The AgentOS routes are protected by Agno's `OS_SECURITY_KEY` bearer-key
mechanism. The app refuses to start if the key is missing or shorter than 32 characters
(generate one with `openssl rand -hex 32`). `GET /health` stays public for infrastructure
monitoring. This key is a **temporary runtime guard**, not product authentication: user
identity, roles and authorization will come in later tasks.

**API docs by environment.** `/docs`, `/redoc` and `/openapi.json` are controlled by Agno's
native `docs_enabled` setting (`DOCS_ENABLED`). The product forces it off when
`APP_ENVIRONMENT` is `staging` or `production`, so those paths return 404 there; in `local`
and `test` they are served unless `DOCS_ENABLED=false`. Because AgentOS only applies
`docs_enabled` to apps it creates itself, `create_app()` passes the same value to our
FastAPI constructor (`docs_url`, `redoc_url`, `openapi_url`).

**Public AgentOS endpoints and `/info`.** Agno intentionally leaves `/` and `/info` public
(plus the docs, where enabled). `/info` is an AgentOS runtime discovery endpoint and may
expose runtime metadata such as component IDs (e.g. `runtime-smoke-test`). Therefore:

- AgentOS is an **internal runtime surface**. A production deployment must **not** expose
  AgentOS directly to the public internet.
- Future product authentication and network routing will sit in front of the runtime.
- The frontend must not consume `/info` or any other AgentOS endpoint directly.

**AgentOS is an internal runtime/API surface.** The frontend must not be designed to depend
directly on AgentOS APIs. Product-facing APIs will sit above the runtime where appropriate.

### Actor & request context (trusted identity boundary)

```
Client
  ↓
Future authentication        (not implemented yet)
  ↓
ActorResolver                (default: NoActorResolver → no actor)
  ↓
ActorContext                 (trusted identity + granted scope)
  ↓
RequestContext               (one per request, on request.state)
  ↓
Product API
  ↓
Permissions / Policy / Tools (later)
```

- **`ActorContext`** (`app/context/models.py`) is who is acting and with what *granted*
  scope: `actor_id`, `actor_type` (`user` \| `api_client` \| `system_agent`), `company_id`,
  `role_ids`, `permissions`, `store_ids`. It is immutable, rejects unknown fields and blank
  IDs, and uses `frozenset`s. Roles such as manager or admin are `role_ids`, not actor types.
- **It holds no effective "allowed actions".** Whether an actor may perform an action will be
  calculated later by the permission layer, policy engine and action rules — never stored
  with the identity.
- **`RequestContext`** holds `request_id` (a UUID generated by the backend), `actor`
  (`None` when unauthenticated), `channel` (`api` \| `web` \| `whatsapp` \| `system`; HTTP
  requests use `api`) and `session_id`.
- **Trusted boundary.** Identity only comes from an `ActorResolver` running on the server.
  It is never taken from prompts or user messages, model output, request bodies, query
  parameters or arbitrary client headers — `X-Actor-Id`, `X-Company-Id`, `X-Role(s)`,
  `X-Permissions`, `X-Store-Ids` and similar are ignored. The LLM is not an identity or
  authorization authority.
- **Fail closed.** Real authentication comes later; until then the production default
  `NoActorResolver` resolves no actor. `create_app(..., actor_resolver=...)` accepts another
  resolver (tests inject a deterministic one from `tests/support/`).
- **Per request, never global.** `RequestContextMiddleware` creates the context for each HTTP
  request and returns the server-generated ID in `X-Request-ID`. An incoming
  `X-Request-ID` is never used as the internal request ID.
- **Route helpers.** `get_request_context` (or the `CurrentRequestContext` alias) returns
  the context; `require_actor_context` (`CurrentActor`) returns the actor or responds
  **401** when there is none. They make no permission (403) decisions.
- **Not sent to the LLM.** Actor context is not added to agent instructions or messages;
  giving agents access to context will be designed explicitly later.
- AgentOS routes remain protected by `OS_SECURITY_KEY` only; they do not require an actor.
- **Not multi-tenant.** Each installation is physically isolated; `company_id` identifies
  the business entity inside that installation. Nothing is persisted: there are no user,
  role, permission, company or session tables for this.

### Agno telemetry policy

No Agno usage telemetry leaves an installation by default:

- Disabled in product code: `AgentOS(..., telemetry=False)`, and every product-created Agno
  `Agent` is built with `telemetry=False` (`runtime-smoke-test`, `generic-reasoning`).
- Agno 3.0.11 lets `AGNO_TELEMETRY` override an agent's flag (at initialization and on each
  run), so startup is **refused** unless it is unset or `false`
  (`app/runtime/telemetry.py`). An attempt to enable it (`true`) fails visibly instead of
  being silently rewritten; ambiguous values (`1`, `yes`, blank, …) are rejected too.
- `AGNO_TELEMETRY=false` stays in `.env.example`/deployment config as defense in depth;
  the code does not depend on it.
- Tests prove it: agents stay `telemetry=False` through a real AgentOS run with the
  variable unset, and no Agno telemetry dispatch or outbound connection happens.

**Model provider calls are not telemetry.** With `APP_DEFAULT_MODEL_PROVIDER=openai` or
`anthropic`, model requests go to that provider by design; an installation is only fully
offline when the provider is `disabled` (or, later, a local model).

### Commerce domain (canonical, provider-independent)

`app/commerce/domain/` is the product's business language. External systems are never our
domain model: adapters (not built yet) will translate into these models.

```
Agents · Workflows · Policies · Analytics
                  ↓ depend on
        Canonical Commerce Domain            (app/commerce/domain)
                  ↑ adapters translate into it (later)
Shopify · WooCommerce · custom ERP · mock systems (later)
```

- **Models:** `Company`, `Store`, `Customer`, `Product`, `Variant`, `Warehouse`,
  `InventoryLevel`, `Order`, `OrderItem`, `Shipment`, plus the value objects `Money` and
  `ExternalReference`. All are immutable Pydantic models that reject unknown fields; their
  collections are immutable too (`frozenset` references, `tuple` order items). References
  serialize in a deterministic order, sorted by `(system, external_id)`.
- **Identity:** canonical IDs are product-owned UUIDs. A provider's IDs live only in
  `ExternalReference(system, external_id)` inside `external_refs`; they never become our
  `id`. Mapping external IDs to canonical ones is left to future adapters/persistence.
- **Statuses:** small canonical enums (`ProductStatus`, `OrderStatus`, `ShipmentStatus`).
  Adapters map source values onto them and keep the original in `source_status`.
- **Exact numbers:** money and quantities are `Decimal` (floats, NaN and Infinity are
  rejected; scale is preserved as given; JSON carries them as strings). Currencies are
  3-letter codes normalized to upper case. There is no exchange-rate logic.
- **Order invariants:** at least one item, positive item quantities, every item priced in
  the order's currency, timezone-aware timestamps. `total` is taken as reported and is
  **not** derived from line prices, because discounts, tax, shipping fees and manual
  adjustments make that equality unsafe.
- **Inventory:** `available` is required; `on_hand`/`reserved` are optional; quantities may
  be negative (backorders, overselling) and no `available = on_hand - reserved` rule is
  imposed, since systems define these differently.
- **Datetimes** must be timezone-aware; naive values are rejected.
- **Customer contact fields** are not format-validated; blank optional strings become
  `None`.
- **Boundaries:** the domain imports only the standard library and Pydantic — no Agno,
  FastAPI, SQLAlchemy or provider SDKs (enforced by `tests/commerce/test_architecture.py`).
  There is no persistence, API, adapter or referential-integrity check yet; cross-entity
  existence checks belong to a later application/repository layer.
- `Company` is the business entity inside one physically isolated installation, not a
  SaaS tenant.

### Company Operating Model (company configuration, not executed)

The **Commerce Domain** describes the universal entities: orders, shipments, products
and inventory. The **Company Operating Model** (`app/company/operating_model/`) describes
how one company operates them: SLAs, escalation thresholds, the KPIs it cares about,
reporting preferences and which product capabilities it enables.

Example: an `Order` with `status=processing` and a company `processing_sla` of 24 hours
(`86400` seconds). A **future** evaluator will combine the two to decide whether the order
is late. Task 006 only defines and validates the configuration; nothing calculates
lateness, raises escalations, computes KPIs or builds reports yet.

```
Canonical Commerce Domain  +  Company Operating Model
                  ↓ (later)
        Policy / evaluation layer   → escalations, KPIs, reports
                  ↓ (later)
        Agents & workflows (enabled per `capabilities`)
```

- **Model:** `CompanyOperatingModel(company_id, version, order_sla, shipment_sla,
  escalations, kpis, reporting, capabilities)`. `company_id` is the canonical `Company.id`
  and `version` is an integer ≥ 1. The version lifecycle, persistence and a loader are
  later work.
- **Durations** hold a `timedelta` in memory and appear in YAML/JSON as whole positive
  **integer seconds**. Floats, booleans, strings and human phrases such as `"2 days"` are
  rejected.
- **SLAs:**
  - `OrderSLAConfig`: optional `confirmation_sla`, `processing_sla` and `fulfillment_sla`,
    plus `late_order_statuses` (canonical `OrderStatus` values, empty by default).
  - `ShipmentSLAConfig`: optional `ready_to_ship_sla` and `ship_to_delivery_sla`, plus
    `terminal_statuses`, which defaults to `delivered`, `returned` and `cancelled`.
- **Escalations:** each `EscalationRule` has an `id`, `name`, `severity`
  (`info`/`warning`/`critical`), `enabled`, a fixed `condition_key` (`order.late`,
  `shipment.late`, `inventory.low`) and a typed `threshold`. The threshold is one of
  `count`, `duration` or `quantity`, and its kind must suit the condition. Rules hold no
  expressions, code or prompts, and there is no rules engine.
- **KPIs:** `enabled_kpis` is a set of operational `KPIKey`s. `primary_kpis` is an ordered
  list without duplicates, and every entry must also be enabled. There are no finance or
  marketing KPIs yet.
- **Reporting:** `timezone`, `default_period` (`today`, `yesterday`, `last_7_days`,
  `last_30_days`), `include_comparison` and `max_highlights` (1–100).
- **Capabilities:** `enabled_agents` is a declarative set (`operations`, `finance`,
  `marketing`, `customer_experience`, `analytics`, `growth`). It never builds or imports
  any agent.
- **Core invariants vs. company config:** rules that always hold (positive item
  quantities, `Decimal` money, timezone-aware timestamps, trusted actor identity) stay in
  code and the domain. Choices that vary per company live here.
- **It is not** provider or model configuration, credentials, authentication, or
  permission policy. It holds no secrets, prompts or URLs.
- **Serialization:** every model is immutable and rejects unknown fields. Sets serialize
  as sorted lists, so JSON/YAML output is deterministic, and round trips are lossless.
- **Example:** `company/operating_model/operating-model.example.yaml` holds generic,
  non-production placeholder data. Tests validate it; the application does not load it.
- **Boundaries:** the package imports only the standard library, Pydantic and the
  commerce domain (enforced by `tests/company/test_architecture.py`).

### Commerce integrations (contract + mock adapter, read-only)

External systems never leak their data models into agents, workflows, policy, analytics
or business logic. Every external system is reached through a product-owned contract,
and an adapter translates provider data into the canonical commerce domain:

```
External provider (mock today; real systems later)
        ↓ provider-specific records (provider IDs, fields, statuses)
Adapter (app/integrations/commerce/mock/adapter.py)   ← validation / trust boundary
        ↓ canonical models only
Canonical Commerce Domain (app/commerce/domain)
        ↓
Application · (later) Tools → Permission/Policy → CommerceIntegration
```

- **Contract:** `CommerceIntegration` (`app/integrations/commerce/contract.py`) is a small
  async `Protocol`. The application, and later tools, depend on it, never on a provider
  API. It offers `get_store`, `get_order`, `list_orders`, `get_shipment`, `list_shipments`
  and `get_inventory`, and exposes a `descriptor`.
  - It knows nothing about actors, permissions, policy, FastAPI or Agno. Authorization
    will wrap these calls in a later layer.
  - The methods are async because real integrations do I/O.
- **Queries:** `OrderQuery` and `ShipmentQuery` are immutable and typed. They filter by
  store or order, by canonical statuses (an empty set means any status), and by time
  range, with a `limit` of 1 to 500.
  - Ranges are half-open (`from <= t < to`) and must have `from < to`. Naive datetimes
    are rejected.
  - A shipment time range only matches shipments that have `shipped_at`.
  - Orders sort by `created_at` then `id`. Shipments sort by `shipped_at`, with unshipped
    ones last, then `id`.
- **Capabilities:** `IntegrationDescriptor(id, name, capabilities)` describes what an
  adapter can do (`orders_read`, `shipments_read`, `inventory_read`). This is metadata,
  not authorization. There are no write capabilities.
- **Errors:** `CommerceIntegrationError` has three subclasses:
  - `IntegrationNotFoundError`: an unknown canonical ID.
  - `IntegrationUnavailableError`: the provider or transport is down.
  - `IntegrationDataError`: provider data cannot be mapped safely; nothing is coerced.

  They carry no HTTP codes and no provider exception types; the original error is chained.
  Inventory for an unknown variant or warehouse raises `IntegrationNotFoundError`. A known
  variant with no stock returns `()`.
- **Canonical ID ≠ provider ID:**
  - Canonical IDs are product-owned UUIDs.
  - The provider's ID is kept only in `ExternalReference(system="mock-commerce",
    external_id=...)`.
  - The mock adapter derives canonical IDs with `uuid5(fixed namespace,
    "<entity type>:<provider id>")`. Repeated reads give the same UUID, and different
    entity types never collide. There is no persistence; real adapters may later persist
    identity mappings.
  - Relationships (store, customer, variant, order, warehouse) use the same derivation,
    so they always point at real mapped entities. Every provider reference must exist and
    belong together, otherwise the read raises `IntegrationDataError`:
    - a shop or warehouse must belong to the mock account;
    - an order's buyer and SKU listings must belong to the order's store;
    - a stocked SKU must have a listing.
- **Statuses:** provider vocabularies map onto canonical statuses. An unrecognized
  provider value becomes `unknown`, with the exact value kept in `source_status`. It is
  never an error.
- **Money and quantities** arrive as strings and become `Decimal`/`Money`, never going
  through `float`.
- **Mock system (development and tests only):**
  - `MockCommerceSystem` is a deterministic, in-memory stand-in for an external
    provider. It uses provider-style string IDs such as `ord_1001`, `ship_501` and
    `sku-tee-red-l`, and its own record types, field names and statuses.
  - Its dataset is 1 account, 2 stores, 6 customers, 5 products, 8 variants,
    2 warehouses, 13 orders, 9 shipments, and stock levels. All timestamps are fixed and
    timezone-aware.
  - The dataset includes edge cases: pending, processing, fulfilled, cancelled and
    unknown-status orders; pending, in-transit, delivered, failed and unknown-status
    shipments; low, zero and negative stock; an order without a customer; and manual
    lines without a SKU.
  - `with_availability(False)` simulates an outage.
  - It uses no network and no credentials.
- **Scope:** read-only. There are no writes, actions, tools, agents, policy, webhooks or
  persistence. No real provider (for example Shopify or WooCommerce) is implemented.

## 3. Technology stack

| Concern | Choice |
|---|---|
| Backend | Python 3.13, FastAPI 0.141.1, Uvicorn 0.54.0 |
| Agent runtime | Agno 3.0.11 — AgentOS + PostgresDb (`agno[os,postgres,openai,anthropic]==3.0.11`) |
| Model providers | Agno native models: OpenAI Responses, Anthropic Claude (optional, `disabled` by default) |
| Python packaging | uv (`pyproject.toml`, `uv.lock`) |
| Config | pydantic-settings 2.15.0 |
| Database | PostgreSQL 17 + pgvector 0.8.6 (SQLAlchemy 2.1 async + psycopg 3) |
| Cache / queue / locks | Redis 8.8 (redis-py 8.1.0) |
| Frontend | Next.js 16.3.6, React 19.3.0, TypeScript 5.9.3 |
| Local infra | Docker Compose |
| Later | S3-compatible file storage, OpenTelemetry |

## 4. Repository structure

```
apps/
  api/            Product API + AgentOS runtime (import package `app`)
  web/            Next.js application
core/             generic core layers (agents, teams, workflows, tools, actions, policies,
                  permissions, approvals, verification, audit) — placeholders
commerce/         placeholder (the canonical domain lives in apps/api/app/commerce/domain)
integrations/     contracts/, adapters/ placeholders (the commerce contract and mock
                  adapter live in apps/api/app/integrations)
company/          per-installation config; operating_model/ holds a non-production example
intelligence/     memory, knowledge, evals, datasets — placeholders
infra/            infrastructure (postgres init scripts)
deployments/      deployment manifests — placeholder
tests/            Python test suite
docker-compose.yml, pyproject.toml, uv.lock, .env.example
```

Placeholder directories contain only a README or `.gitkeep`; Python packages are created only
where code exists.

## 5. Local prerequisites

- Python 3.13 (uv can install it: `uv python install 3.13`)
- [uv](https://docs.astral.sh/uv/) ≥ 0.8
- Node.js ≥ 20.9 (22 LTS recommended) and npm
- Docker with Docker Compose v2

## 6. Install dependencies

```bash
cp .env.example .env          # then edit local values; .env is git-ignored
uv sync                       # Python deps from uv.lock (includes dev group)
cd apps/web && npm ci         # frontend deps from package-lock.json
```

### Environment variables

| Variable | Used by | Purpose |
|---|---|---|
| `POSTGRES_DB`, `POSTGRES_USER` | compose | Local database name / user |
| `POSTGRES_PASSWORD` | compose | **Required.** Local-only database password |
| `POSTGRES_PORT`, `REDIS_PORT` | compose | Host ports (bound to 127.0.0.1) |
| `APP_NAME` | API | Application name |
| `APP_ENVIRONMENT` | API | `local` \| `test` \| `staging` \| `production` |
| `APP_DEBUG`, `APP_LOG_LEVEL` | API | Debug flag, log level |
| `APP_API_HOST`, `APP_API_PORT` | API | Bind address documentation / defaults |
| `APP_DATABASE_URL` | API | **Required.** PostgreSQL DSN (`postgresql+psycopg://...`) |
| `APP_AGNO_DB_SCHEMA` | API | Schema for Agno runtime tables (default `agno_runtime`) |
| `APP_REDIS_URL` | API | Optional Redis DSN (not used yet) |
| `OS_SECURITY_KEY` | Agno | **Required**, ≥ 32 chars. Bearer key for AgentOS routes (read by Agno) |
| `DOCS_ENABLED` | Agno | Optional (default `true`). Always off in `staging`/`production` |
| `APP_DEFAULT_MODEL_PROVIDER` | API | `disabled` (default) \| `openai` \| `anthropic` |
| `APP_DEFAULT_MODEL_ID` | API | Required when a provider is selected; no default |
| `OPENAI_API_KEY` | Agno (OpenAI) | Required only when the provider is `openai` |
| `ANTHROPIC_API_KEY` | Agno (Anthropic) | Required only when the provider is `anthropic` |
| `AGNO_TELEMETRY` | Agno | Optional; unset or `false` only. Anything else (e.g. `true`) stops startup |
| `NEXT_PUBLIC_API_BASE_URL` | web | API base URL (reserved for later use) |

## 7. Start PostgreSQL / Redis

```bash
docker compose up -d
docker compose ps             # both services should become "healthy"
docker compose down           # stop (add -v to delete volumes)
```

The pgvector extension is enabled on first volume initialisation
(`infra/postgres/init/01-extensions.sql`). The `agno_runtime` schema is created by Agno when
the API starts; no product schemas are created.

## 8. Run the API

Set `OS_SECURITY_KEY` in `.env` first (`openssl rand -hex 32`); the API refuses to start
without `APP_DATABASE_URL` or without a key of at least 32 characters. PostgreSQL must be running (section 7).

```bash
uv run uvicorn app.main:create_app --factory --app-dir apps/api --env-file .env --reload --port 8000
curl http://localhost:8000/health
```

Example `/health` response:

```json
{
  "status": "ok",
  "application": {"name": "commerce-ai-platform", "version": "0.1.0", "environment": "local"},
  "agent_runtime": {"framework": "agno", "version": "3.0.11", "status": "ready"}
}
```

### Authenticate to AgentOS locally

```bash
export OS_SECURITY_KEY=...   # the value from your .env
curl -i http://localhost:8000/agents                                           # 401
curl -H "Authorization: Bearer $OS_SECURITY_KEY" http://localhost:8000/agents  # 200
```

### Inspect the AgentOS API docs

Open <http://localhost:8000/docs> (Swagger UI) or <http://localhost:8000/redoc> — available in
`local`/`test` only. Product and AgentOS routes appear together. Use **Authorize** with the security key to call AgentOS routes.

### Verify runtime/session persistence

Without calling any model, create an empty session through the native AgentOS API, read it
back, and look at it in PostgreSQL:

```bash
H="Authorization: Bearer $OS_SECURITY_KEY"
SID=$(curl -s -H "$H" -H 'Content-Type: application/json' \
  -d '{"agent_id":"runtime-smoke-test","session_name":"persistence-check"}' \
  'http://localhost:8000/sessions?type=agent' | python3 -c 'import json,sys;print(json.load(sys.stdin)["session_id"])')
curl -s -H "$H" "http://localhost:8000/sessions/$SID?type=agent"
docker compose exec postgres psql -U platform -d platform \
  -c "SELECT session_id, agent_id, created_at FROM agno_runtime.agno_sessions;"
curl -s -X DELETE -H "$H" "http://localhost:8000/sessions/$SID?type=agent"   # clean up
```

The integration test `tests/integration/test_agentos_postgres.py` automates this, including
reading the session back from a fresh application instance.

### Optional: live model inference (manual)

Not required for development or CI. With your own provider key, set in `.env`:

```bash
APP_DEFAULT_MODEL_PROVIDER=anthropic        # or openai
APP_DEFAULT_MODEL_ID=<a current model ID from your provider>
ANTHROPIC_API_KEY=<your key>                # or OPENAI_API_KEY for openai
```

Restart the API (the `--env-file .env` flag loads the key into the process environment,
where Agno reads it), then call the native AgentOS run endpoint:

```bash
curl -s -H "Authorization: Bearer $OS_SECURITY_KEY" \
  -F message='Rewrite as one sentence: The review moved to Tuesday. It starts at 10:00.' \
  -F stream=false \
  http://localhost:8000/agents/generic-reasoning/runs
```

The response contains `content`, `run_id` and `session_id`; the run is stored in
`agno_runtime.agno_runs`. This makes a real, billable provider call.

## 9. Run the frontend

```bash
cd apps/web
npm run dev                   # http://localhost:3000
```

## 10. Run tests

```bash
uv run pytest                 # unit tests; integration tests skip without a database
# With PostgreSQL running (section 7), run everything including integration tests:
set -a; . ./.env; set +a; uv run pytest
uv run ruff check . && uv run ruff format --check .
cd apps/web && npm run typecheck && npm run build
docker compose --env-file .env.example config --quiet   # validate compose
```

CI (`.github/workflows/ci.yml`) runs the backend, frontend and infrastructure checks above on
every pull request and on pushes to `main`. The backend job starts PostgreSQL via Compose and
runs the integration tests (they must not skip in CI), then boots the API with a CI-only
`OS_SECURITY_KEY` and checks `/health` and AgentOS authentication. The infrastructure job starts
PostgreSQL/Redis and verifies they are healthy and that pgvector is enabled.

**CI never calls a model provider.** No provider keys exist in CI and the default provider is
`disabled`. Agent execution is tested with a TEST-ONLY `DeterministicModel`
(`tests/support/deterministic_model.py`, an Agno `Model` returning a fixed response): the
`generic-reasoning` agent runs through the native `POST /agents/generic-reasoning/runs`
endpoint against PostgreSQL, with a guard that fails the test on any outbound connection,
and the run and session are read back from `agno_runtime`. Provider factory tests only
construct `OpenAIResponses`/`Claude` objects with dummy values; nothing is sent.
