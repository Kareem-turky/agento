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
Authorization: Bearer <Product API key>
  ↓
ActorResolver                (ProductApiKeyActorResolver when APP_PRODUCT_AUTH_MODE=api_key;
                              NoActorResolver when auth is disabled, local/test only)
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
- **Fail closed.** Product authentication is the configured API-key resolver (see
  "Product authentication" below). With auth disabled (local/test only) `NoActorResolver`
  resolves no actor. `create_app(..., actor_resolver=...)` uses an injected resolver
  exactly (tests inject a deterministic one from `tests/support/`).
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

### Product authentication: API keys (single company)

Product routes authenticate with `Authorization: Bearer <Product API key>`:

```
Authorization header (exactly one; scheme "Bearer", any case; one token)
  → raw key well formed (32–256 printable ASCII, no whitespace; case-sensitive)
  → SHA-256 → hmac.compare_digest against every configured hash (no early exit)
  → exactly one match → ActorContext(actor_type="api_client", company_id=APP_COMPANY_ID,
                                      actor_id / role_ids / permissions / store_ids of that key)
  → otherwise no actor → the usual 401 "Not authenticated" (the reason is never revealed)
```

- **Two separate credentials.** The Product API key authenticates Product routes only;
  `OS_SECURITY_KEY` authenticates AgentOS routes only. Neither opens the other surface,
  and they must differ: the app refuses to start if a configured Product key is the
  `OS_SECURITY_KEY` (its hash is checked against every configured key hash in constant
  time; the error names no key). The Product paths are exempted from the AgentOS layer by
  exact path; that does not make them public.
- **One company per deployment.** `APP_COMPANY_ID` is the only company; every configured
  principal belongs to it (a key has no company of its own). No tenants.
- **Configuration holds hashes only.** `APP_PRODUCT_API_KEYS` is a JSON array of
  principals: `key_id` (operator metadata, never exposed), `key_sha256` (64 lowercase hex),
  `actor_id`, `role_ids`, `permissions`, `store_ids`. The raw key never enters settings,
  logs, contexts, audit events or any response; it never leaves the resolver. Unknown
  fields, malformed hashes and duplicate hashes or `key_id`s are refused at startup.
- **Fail closed.** `APP_PRODUCT_AUTH_MODE=api_key` requires `APP_COMPANY_ID` and at least
  one principal. Staging and production refuse to start with Product auth `disabled`;
  `disabled` (`NoActorResolver`) is for local/test only.
- **Authentication is not authorization.** A valid key supplies identity and grants only;
  the exact store check still applies (403) and Governance still decides every action
  (a key without `tickets.create` gets a durable `denied`). The resolver decides nothing.
- **Health stays public** and reveals nothing about keys, actors or grants.
- **Creating a key.** Generate a random key (32–256 printable ASCII characters) and
  compute its hash without putting it on the command line:

  ```bash
  uv run python apps/api/scripts/hash_product_api_key.py   # prompts; input is hidden
  ```

  It reads the key from stdin and prints only the lowercase SHA-256. Put that hash in
  `APP_PRODUCT_API_KEYS`; give the raw key to the client only.
- **Rotation** is a configuration change: add the new principal, deploy/restart, move the
  client, then remove the old one. There is no key database, key-management API, JWT,
  OAuth or session.

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
- **Scope:** `CommerceIntegration` is read-only: no tools, agents, policy, webhooks or
  persistence. No real provider (for example Shopify or WooCommerce) is implemented.
  The only write is the separate `TicketingIntegration` contract, reached through
  the governed `operations.ticket.create` action (see below).

#### Commerce integration conformance harness (every adapter must pass it)

`CommerceIntegration` is the provider-independent boundary: the Product contract is
authoritative, and a provider adapter adapts itself to it (never the reverse). The
reusable, offline, test-only harness in `tests/commerce_conformance/` checks any
adapter through the contract alone (it imports no concrete adapter):

- **canonical identity:** exact canonical ids, valid stores/orders/shipments/levels,
  aware timestamps, provider ids never in canonical fields, relationships resolvable
  (order -> store, shipment -> order);
- **query semantics:** store/order/status/time filters combined with AND, checked
  against a reference computed from the adapter's own unfiltered results;
- **deterministic sorting** (orders by created_at then id; shipments by shipped_at,
  unshipped last, then id; inventory by warehouse) and **limit after filter + sort**;
- **half-open time windows** (`from <= t < to`; unshipped never matches a time bound);
- **store isolation:** store-scoped orders and shipments (via the parent order) never
  leak another store's data; unknown ids in list queries give an empty tuple;
- **error translation:** unknown entity -> `IntegrationNotFoundError`, unreachable ->
  `IntegrationUnavailableError`, unmappable data -> `IntegrationDataError`; no provider
  or transport exception escapes;
- **data minimization:** credentials, URLs, auth headers and raw payloads planted in
  the provider never appear in a Product-level error;
- **read-only capabilities** and deterministic repeated reads.

Negative self-tests prove the harness catches wrong sorting, cross-store leaks,
end-inclusive windows, list-instead-of-tuple and escaping provider exceptions.

**Adding a commerce adapter:** (1) implement `CommerceIntegration`; (2) return
canonical domain models only; (3) write a provider-specific
`CommerceConformanceFixture` (see `tests/integrations/mock_conformance.py`); (4) pass
the generic harness (`CONFORMANCE_CHECKS`, one parametrized test); (5) pass
provider-specific mapping and security tests; (6) only then add it to the deployment
composition. Passing conformance is necessary but NOT sufficient for production:
provider authentication, rate limits, retries, pagination, idempotency and
operational security still require provider-specific review. No real provider
adapter exists yet; none is added without its authoritative API contract.

### Governance: actions, permissions and baseline policy (decision only)

`app/governance/` decides whether a trusted actor may take an action, and on what
terms. It is pure decision logic: nothing is executed, no integration is called, and
no approval or audit record is stored. The decision is the end of the flow.

```
untrusted ActionIntent(name)
        ↓  GovernanceGate: trusted ActionCatalog lookup
        ↓  (unknown name → DENY / UNKNOWN_ACTION, no permission check)
trusted ActionDefinition (risk, required permission, scope requirement)
        ↓  PermissionEvaluator.evaluate(actor, action, scope)
PermissionDecision (allowed, reason, action_name, required_permission)
        ↓  BaselinePolicyEvaluator.evaluate(action, permission)
PolicyDecision (ALLOW / DENY / REQUIRE_APPROVAL + reason)
```

- **Trusted and untrusted inputs:**
  - `ActionIntent` is untrusted and carries only a `name`. Governing fields such as risk,
    permission, scope requirement, approval, permissions, roles, actor, company or
    store are rejected.
  - Everything that governs a decision comes from trusted backend objects: the
    `ActionDefinition` in the `ActionCatalog`, the existing `ActorContext` from
    `app.context` (there is no second actor model), and the `ActionScope` target
    (company, optional store) that backend code resolves.
- **Unknown actions:** `GovernanceGate` denies an unknown action name (`UNKNOWN_ACTION`,
  with no risk and no permission decision) before any permission evaluation.
  `PermissionEvaluator` only ever receives a trusted `ActionDefinition`; it knows
  nothing about the catalog or intents.
- **Permission checks** run in this order, and the first failure wins:
  1. no actor → `NO_ACTOR`
  2. the actor's company is not the target company → `COMPANY_MISMATCH`
  3. the exact required permission is not held → `MISSING_PERMISSION`. There are no
     wildcards and no prefix matching, and roles or actor type grant nothing, so
     `role_ids={"admin"}` alone is denied.
  4. STORE actions only: no target store → `STORE_SCOPE_MISSING`; the target store is
     not in `actor.store_ids` → `STORE_NOT_PERMITTED` (an empty set grants no stores)
  5. otherwise → `GRANTED`

  COMPANY actions are not constrained by stores: neither `scope.store_id` nor
  `actor.store_ids` affects them.
- **Baseline policy.** There is a single deterministic baseline, based only on the
  trusted risk:

  | Case | Outcome |
  |---|---|
  | Unknown action | `DENY` (`UNKNOWN_ACTION`) |
  | Permission denied | `DENY` (never `REQUIRE_APPROVAL`) |
  | `READ` | `ALLOW` |
  | `LOW_RISK_WRITE` | `ALLOW` |
  | `MEDIUM_RISK` | `REQUIRE_APPROVAL` |
  | `HIGH_RISK` | `REQUIRE_APPROVAL` |

  There is no per-action or company-specific approval override yet. MEDIUM and HIGH
  require approval purely because of the baseline risk policy.
- **Not built yet:** approval workflow and persistence, tools and configurable policy
  are later work. Governed execution, verification and audit events live in
  `app/execution/` (below).
- **Boundaries:** the package imports only the standard library, Pydantic and
  `app.context.models`. `tests/governance/test_architecture.py` enforces this.

### Governed execution, verification and audit (foundation)

`app/execution/` runs an allowed action through a fixed pipeline. It adds no real
commerce actions, no approval workflow, no persistence and no Agno tool: handlers are
registered by backend code, and tests use fakes only.

```
RequestContext (trusted actor) + ActionIntent(name) + ActionScope + raw parameters
        ↓  Govern:   GovernanceGate.decide(request.actor, intent, scope)
        ↓            DENY → DENIED · REQUIRE_APPROVAL → AWAITING_APPROVAL (nothing runs)
        ↓  Lookup:   ActionHandlerRegistry.get(policy.action_name) — exact name only
        ↓            missing → FAILED / handler_not_registered
        ↓  Validate: handler.validate(read-only copy of raw params) → frozen model
        ↓            invalid → FAILED / input_invalid (no execute, no verify)
        ↓  Execute:  handler.execute(validated input) → ExecutionResult(reference_id)
        ↓            confirmed no effect → FAILED (the only case that skips Verify)
        ↓  Verify:   handler.verify(validated input, result | None) → VerificationResult
        ↓  Audit:    AuditSink.record(event) at every step
ActionRun (status + typed reason; VERIFIED only when every step succeeded)
```

- **Trust:** `request_context.actor` is the only actor authority. Execution parameters
  are untrusted JSON: they never reach governance, cannot change the actor, company,
  store, permissions, risk or handler, and reach `execute` only as the handler's
  validated, immutable model.
- **Handlers** implement the `ActionHandler` protocol: `action_name`,
  `validate(parameters)`, async `execute(context, validated_input)` and async
  `verify(context, validated_input, execution_result | None)`. `ActionHandlerRegistry`
  is immutable, rejects duplicate names and is passed in explicitly; there is no
  global registry.
- **Trusted execution context.** The coordinator builds exactly one frozen
  `ActionExecutionContext` per run (run id, request id, action name, actor id/type,
  company, store, channel, session) from the trusted `RequestContext`, the trusted
  `ActionScope` and its own run id, and passes the same object to `execute` and
  `verify`. Raw parameters never reach it, so they cannot choose company, store,
  actor or run.
- **Terminal statuses and reasons:**

  | Situation | Status | Reason |
  |---|---|---|
  | Governance denies | `denied` | `policy_denied` |
  | Governance requires approval | `awaiting_approval` | `approval_required` |
  | No registered handler | `failed` | `handler_not_registered` |
  | Invalid parameters | `failed` | `input_invalid` |
  | Validator returned a mutable or non-model value | `failed` | `handler_contract_violation` |
  | Pre-execution audit write failed | `failed` | `audit_unavailable` |
  | Execution failed and confirmed no effect | `failed` | `execution_failed_no_effect` |
  | Execution failed, effect unknown (verified anyway, see below) | `requires_human` | `execution_outcome_uncertain` |
  | Verification returned `verified=False` | `requires_human` | `verification_failed` |
  | Verification raised or returned garbage | `requires_human` | `verification_error` |
  | Verified, but a post-execution audit write failed | `requires_human` | `audit_incomplete` |
  | Everything succeeded and was audited | `verified` | `verified` |

  Handlers signal a confirmed no-effect failure by raising `ExecutionFailedWithoutEffect`.
  Any other exception, or an invalid return value, is treated as uncertain.
- **Every execute attempt that may have produced a side effect triggers an independent
  verification attempt.** Only a handler-confirmed no-effect failure skips it.
  - After a completed execute, `verify(validated_input, execution_result)` receives the
    safe receipt.
  - After an uncertain execute (timeout, crash, invalid return), `verify(validated_input,
    None)` runs with no receipt and must inspect the target state from the validated
    input alone. Its answer is recovery evidence for a human: a valid
    `VerificationResult` is kept on the `ActionRun` and its `reason_code` is audited, but
    the run stays `requires_human` / `execution_outcome_uncertain` even when
    `verified=True`. A verifier error leaves `verification_result=None`.
  - Audit write failures on this path never skip the verification attempt; they only
    set `audit_complete=False`.
- **Results are references only.** `ExecutionResult` carries a safe `reference_id`, never
  raw provider data. `VerificationResult` is `verified` plus a short `reason_code`.
- **Audit** events are metadata only: ids, time, event type, action, trusted actor and
  scope, policy outcome and reason, run status and reason, reference id and
  verification code. They never hold raw parameters, provider data, secrets or
  exception messages. `AuditSink` is a protocol passed in explicitly; tests use a
  recording sink. Audit failure semantics:
  - before execution (`requested`, `execution_started`, or `policy_decided` on an ALLOW
    decision): the run stops with `failed` / `audit_unavailable` and nothing is executed.
    If `policy_decided` fails on a DENY or REQUIRE_APPROVAL decision, that governance
    outcome is kept and `audit_complete=False` (nothing can execute either way);
  - after execution: verification still runs, the run is never `verified`,
    `audit_complete=False`, and the status becomes `requires_human`.
- **Durable audit log (foundation).** `app.persistence.PostgresAuditSink` implements
  `AuditSink` on `product.audit_events` (migration `0002`). Each `record(event)`
  accepts only an `AuditEvent` and runs one short transaction: one `INSERT`, then
  `COMMIT`, before it returns. It never buffers, queues, retries or upserts; a
  duplicate `event_id` fails and the stored row is unchanged. Every failure is the
  generic `AuditPersistenceError` (no driver, SQL or connection details). Because
  `record` returns only after the commit, `execution_started` is durable *before*
  the handler's side effect runs; if it cannot be stored, nothing is executed
  (`failed` / `audit_unavailable`), and any later audit failure prevents a false
  `verified` (`requires_human` / `audit_incomplete`). Rows hold exactly the
  metadata fields above plus a server-side `recorded_at`; there are no parameter,
  payload, provider, idempotency-key or credential columns, and no foreign keys.
  The audit table is separate from `product.write_commands`: a command replay adds
  no audit rows because nothing executes. There is no audit read API or HTTP route,
  and the sink is not yet wired into `create_app` (composition is later work).
- **Boundaries:** the package imports only the standard library, Pydantic,
  `app.governance` and `app.context.models`. `tests/execution/test_architecture.py`
  enforces this.

### First governed business write: `operations.ticket.create`

`app/operations/` holds product business actions built on governance and execution.
Task 010 adds exactly one real write, proving the whole path with a real (mock)
external system:

| Action | Permission | Risk | Scope |
|---|---|---|---|
| `operations.ticket.create` | `tickets.create` | `LOW_RISK_WRITE` (baseline ALLOW) | `STORE` |

```
RequestContext (trusted actor) + ActionIntent("operations.ticket.create")
  + ActionScope(company UUID, store UUID) + raw {title, description}
        ↓  GovernanceGate (exact permission tickets.create, company, granted store)
        ↓  CreateOperationalTicketHandler.validate → frozen {title, description}
        ↓  ExecutionCoordinator builds the trusted ActionExecutionContext
        ↓  execute: TicketingIntegration.create_ticket(company, store from context,
        ↓           correlation_id = run id) → Mock Ticketing → canonical Ticket
        ↓  verify: independent re-read find_ticket_by_correlation(run id), compared
        ↓          with trusted scope + validated input (+ reference id if any)
        ↓  audit (metadata only)
ActionRun: VERIFIED · REQUIRES_HUMAN · FAILED · DENIED
```

- **Input is business content only.** `CreateOperationalTicketInput` is `title`
  (1–160 chars, trimmed) and `description` (1–4000 chars, trimmed); unknown fields
  are rejected. Raw parameters cannot choose company, store, actor, permissions,
  risk, approval, run or correlation: those come from the trusted context, and a
  request that tries fails as `failed / input_invalid` with no write.
- **Trusted scope.** The ticket is created for the `ActionScope` company and store,
  which must be canonical UUID strings. A malformed trusted scope fails before any
  external call (`failed / execution_failed_no_effect`); nothing is guessed.
- **Canonical `Ticket`** (`app/commerce/domain/tickets.py`): `id`, `company_id`,
  `store_id`, `title`, `description`, `status` (`open`, `resolved`, `cancelled`,
  `unknown`), `created_at`, `external_refs`. The provider ticket ID lives only in
  `ExternalReference` (system `mock-commerce`); it is never the canonical `id`.
- **`TicketingIntegration`** (`app/integrations/commerce/ticketing.py`) is the
  product-owned write contract: `create_ticket`, `get_ticket`,
  `find_ticket_by_correlation`. Write failures are typed:
  `IntegrationWriteRejectedError` (nothing was written) maps to
  `ExecutionFailedWithoutEffect`; `IntegrationWriteUncertainError` (may have been
  written) and any unexpected error map to `ExecutionOutcomeUncertain`. Provider
  error text never crosses the contract.
- **Mock ticketing** (`MockTicketDesk` + `MockTicketingAdapter`) shares the Mock
  Commerce identity: tickets belong to the same canonical company/store UUIDs the
  commerce adapter returns. The provider key is `tkt_<correlation>`, so the same
  correlation never creates a duplicate; timestamps come from an injectable clock.
  The desk is separate from the read dataset, so orders, shipments, inventory and
  stores are never mutated. Test modes: `normal`, `confirmed_no_effect`,
  `uncertain_after_write` (the ticket is stored, then the desk times out) and
  `altered_on_write` (the stored ticket differs from the request).
- **The execute response alone is not proof.** Verification re-reads the ticket by
  run correlation. After an uncertain write the re-read still runs and its evidence
  is kept, but the run stays `requires_human / execution_outcome_uncertain`.
- **Connections:** Task 010 itself added no agent or HTTP endpoint. Since Task 011
  the Operations Agent (below) reaches this action through `ExecutionCoordinator`;
  there is still no HTTP action endpoint, persistence, Redis or approval workflow.

### Operations Agent (Agno) with governed product tools

`app/agents/operations*.py` holds a real Agno agent (`id="operations"`, "Operations
Agent") that analyzes an order and its shipments and can request an operational
ticket. It is wired programmatically only:

```
OperationsAgentRunner.run(request, scope, message,     # trusted request + store scope
                          requested_write_actions=...)  # trusted per-run write intent
  → TrustedOperationsRunContext(request, scope, requested_write_actions)  # runner only
  → Agent.arun(message, dependencies={operations_run_context: <object>},
               add_dependencies_to_context=False)
  → Agno tool loop → product tools (RunContext injected by Agno)
      get_order            → GovernanceGate(operations.order.read)     → CommerceIntegration
      get_order_shipments  → GovernanceGate(operations.shipments.read) → CommerceIntegration
      get_daily_operations_report(business_date?) → DailyOperationsReportService
                                  (the deterministic workflow: governance, business day,
                                   metrics, findings; the tool computes nothing)
      create_operational_ticket → requested for this run? (no → action_not_requested)
                                  → ExecutionCoordinator(operations.ticket.create)
                                  → GovernanceGate (tickets.create?) → handler
                                  → TicketingIntegration → verify → audit
  → final answer
```

| Action | Permission | Risk | Scope |
|---|---|---|---|
| `operations.order.read` | `orders.read` | `READ` | `STORE` |
| `operations.shipments.read` | `shipments.read` | `READ` | `STORE` |
| `operations.ticket.create` | `tickets.create` | `LOW_RISK_WRITE` | `STORE` |

- **Tools are not authorization.** Every read is governed by `GovernanceGate`
  before any integration call, and the only write goes through
  `ExecutionCoordinator`. Having a tool, a role name, the agent id or an Agno
  `user_id`/`session_id` grants nothing.
- **Actor permission and per-run write intent are separate, and both are required.**
  `tickets.create` (GovernanceGate) answers "may this actor perform this action?".
  The trusted `requested_write_actions` of the run answers "was this write
  requested for this particular agent run?". It defaults to empty (a read-only run);
  the runner's caller supplies it as trusted application-layer intent (the
  product-facing boundary that sets it comes later). If the ticket action is not
  requested, `create_operational_ticket` returns
  `{"status": "denied", "reason": "action_not_requested"}` without entering
  `ExecutionCoordinator`: nothing is executed, verified or audited. If it is
  requested, governance still decides. The LLM answers neither question: the
  instruction to create tickets only when asked is guidance, not the enforcement,
  and the write intent is never shown to the model or exposed as a tool argument.
- **Trusted context comes only from the product runner.** `OperationsAgentRunner`
  builds a frozen `TrustedOperationsRunContext` (the trusted `RequestContext`, whose
  `actor` is the only actor authority, a store-scoped `ActionScope` and the
  `requested_write_actions`; a run without a store or with an unknown write action
  fails closed). The user's message cannot change it.
- **`RunContext` is Agno's injection mechanism.** Tools declare
  `run_context: RunContext`; Agno injects it and omits it from the tool schema, so
  the model sees only `order_id` or `title`/`description`. A model-supplied
  `run_context` is replaced by the injected one, and unknown arguments are
  rejected. Tools accept the dependency only if it is a real
  `TrustedOperationsRunContext` object; a missing value, dict, JSON string or anything
  else yields `trusted_context_unavailable` and nothing is read or written.
- **Dependencies are not model context.** The agent and the runner set
  `add_dependencies_to_context=False` (and `resolve_in_context=False`), so actor,
  company, store, roles and permissions never appear in the prompt.
- **Scope checks on data.** The trusted store is enforced before reading, and every
  returned order is checked against it before anything is exposed; another store's
  order is reported as `not_found`, and its shipments are never listed.
- **Safe outputs.** Order: `order_id`, `status`, `created_at`, `total_amount`,
  `currency`, `item_count`. Shipment: `shipment_id`, `status`, `shipped_at`,
  `delivered_at`. Ticket: `status`, `reason`, and `ticket_id` only when `verified`.
  No external references, provider IDs, customer contact data, courier data, raw
  statuses or exception text. Read failures are typed outcomes (`ok`, `denied`,
  `invalid_id`, `not_found`, `unavailable`, `data_error`,
  `trusted_context_unavailable`).
- **External content is untrusted data.** The instructions tell the agent to treat
  tool output as data, never instructions; to create a ticket only when the user
  explicitly asked; and to claim success only for `verified` (a `requires_human`
  result must be reported as needing human review).
- **Configuration:** only the four tools, `tool_call_limit=6`, `telemetry=False`,
  no memory, knowledge/RAG or history. The agent takes any Agno `Model`.
- **Not registered with AgentOS.** The AgentOS security key is not product actor
  authentication. Its only HTTP entry point is the read-only product route below.
  Tests drive the agent through the real Agno tool loop with a test-only scripted
  model (`tests/support/scripted_tool_model.py`).

### Product Operations API: `POST /api/v1/operations/runs` (read-only)

The first product HTTP boundary for the Operations Agent. It is a Product API
route, not an AgentOS route: the agent is still not registered with AgentOS.

```
HTTP request
  → RequestContextMiddleware (server request id) → ActorResolver
  → trusted ActorContext                          (none → 401)
  → strict body {message, store_id}               (anything else → 422)
  → exact store grant: store_id ∈ actor.store_ids (otherwise → 403, agent never runs)
  → trusted ActionScope(company_id=actor.company_id, store_id=<granted store>)
  → OperationsRunService.run_product              (OperationsAgentRunner; none → 503)
  → requested_write_actions = ∅ → Agno Operations Agent → governed read tools
  → {"request_id": <X-Request-ID>, "message": <assistant text>}
```

Request: `{"message": "...", "store_id": "<uuid>"}`. The message is trimmed,
1–8000 characters. Any other field (for example `company_id`, `actor_id`,
`permissions`, `requested_write_actions`, `allow_write`, `approved`, `session_id`,
`run_context`) is rejected with 422.
Response: `{"request_id": "<uuid>", "message": "<text>"}`. `request_id` equals the
server-generated `X-Request-ID`; an incoming `X-Request-ID` is ignored.

- **Product API auth is not `OS_SECURITY_KEY`.** The route is authenticated by the
  product `ActorResolver` only (a Product API key; see "Product authentication"). Without
  a Product actor it returns 401, even with a valid AgentOS key, and it needs no AgentOS
  key. Its exact path is exempted from the AgentOS auth layer (Agno's
  `AuthorizationConfig.excluded_route_paths`; exact paths only, no wildcards), and
  every AgentOS route still requires the key.
- **Store and company.** `store_id` is a client-selected target. It becomes trusted
  scope only after an exact membership check against `actor.store_ids` (no
  wildcards: `*`, `all` or an empty set grant nothing). A store not granted,
  existing or not, is a generic 403 and the agent never runs. `company_id` always
  comes from `ActorContext.company_id`; the request has no company field. The body
  and prompt cannot supply identity, permissions or write intent.
- **Read-only.** `run_product` always runs with `requested_write_actions` empty. The
  ticket write still exists internally (programmatic `OperationsAgentRunner.run`)
  but is not exposed over HTTP: even a model that calls `create_operational_ticket`
  gets `action_not_requested`, and `ExecutionCoordinator` is never entered. The
  only HTTP write is the separate, deterministic ticket endpoint below, which
  involves no agent or model.
- **Tool permissions still apply.** Store access only selects the target;
  `orders.read` and `shipments.read` are still enforced by the governed tools.
- **Safe results and errors.** Only the final assistant text is returned, never the
  Agno `RunOutput`, tool calls, context or policy data. That text is display text,
  never an authorization decision. With no service configured the route returns
  503; there is no mock fallback. A failed run (for example a model error) or a
  service exception also gives a generic 503 with no internal detail, and every
  response carries `X-Request-ID`.
- **Not included:** sessions, chat history, streaming, a frontend, an
  `Idempotency-Key` header or any write.

### Durable write commands and PostgreSQL idempotency

The same logical write must not execute twice because of HTTP or client retries,
timeouts, restarts, concurrent duplicates or several API processes. The
`WriteCommandCoordinator` (`apps/api/app/commands/`) is a new application layer
*above* `ExecutionCoordinator` that makes this durable:

```
trusted caller (RequestContext + ActionScope), untrusted intent + parameters, caller's key
  → reject before any claim: no actor · invalid key · unknown action · READ action ·
    parameters that are not plain JSON
  → ONE detached, validated plain-JSON snapshot of the parameters (the caller's mapping
    is never read again)
  → SHA-256(key) + SHA-256(canonical {action_name, company_id, store_id, snapshot})
  → WriteCommandStore.claim  (PostgreSQL, atomic; commits IN_PROGRESS before returning)
      same key, same fingerprint  → REPLAY: the stored command, replayed=true, never executed
      same key, other fingerprint → IdempotencyConflictError, never executed
      new key                     → NEW
  → ExecutionCoordinator.run(snapshot) (govern → validate → execute → verify → audit), once
  → WriteCommandStore.complete (terminal status, reason, action_run_id, reference, audit)
  → WriteCommandResult
```

- **PostgreSQL is the source of truth.** `product.write_commands` has one
  uniqueness boundary, `UNIQUE (company_id, actor_id, idempotency_key_hash)`. The
  claim is `INSERT … ON CONFLICT DO NOTHING RETURNING …`, then a read of the
  existing row; concurrent claims yield exactly one NEW. Nothing is cached in
  memory or in Redis. The claim transaction is short and is committed before
  execution starts; no transaction stays open while the action runs.
- **What executes is what was fingerprinted.** The parameters are copied once into a
  new, recursively detached plain-JSON tree before the claim; that same snapshot is
  fingerprinted and executed. Mutating the caller's mapping (at any depth) while the
  claim is awaited, or a mapping whose values change between reads, cannot make the
  executed request differ from the claimed one. The snapshot is never persisted.
- **Namespace and fingerprint.** A key identifies one logical command of one actor
  in one company. Store, action and parameters are in the request fingerprint,
  not the key, so reusing a key for another store, action or parameters is a
  conflict (future HTTP: 409), never a second command.
- **Nothing sensitive is stored.** Only `SHA-256(key)` and the fingerprint hash are
  persisted: never the raw key, ticket title/description, prompts, messages,
  permissions, provider data, exception text or audit payloads. The key is
  1–128 characters of `A–Z a–z 0–9 . _ : ~ -`, case-sensitive, never normalized
  and never generated by the backend. It is not authorization: authority comes
  from the trusted request, and governance decides every NEW command.
- **Replays never execute**, whatever the stored status: VERIFIED, DENIED,
  AWAITING_APPROVAL, FAILED, REQUIRES_HUMAN, and IN_PROGRESS. A replay returns the
  original outcome even if the actor's permissions changed since; a new attempt
  needs a new key.
- **Statuses.** Terminal statuses map one-to-one from `ActionRunStatus`;
  IN_PROGRESS exists only in the command layer. VERIFIED remains the only
  confirmed success.
- **Conservative failures.** If `ExecutionCoordinator` raises after the claim, the
  command is recorded REQUIRES_HUMAN / `command_execution_error` (no exception
  text). If recording the terminal state fails, the caller gets REQUIRES_HUMAN /
  `command_persistence_incomplete` with `persistence_complete=false`, and the
  durable row stays IN_PROGRESS, so a retry replays IN_PROGRESS and never
  re-executes.
- **IN_PROGRESS after an interrupted process is safe but unresolved.** Because raw
  parameters are not stored, a command cannot be resumed after a crash. There is
  no timeout-based retry, stale-claim takeover, worker or cleanup job: recovery (or
  a human) handles it later.
- **Persistence is infrastructure.** `app/persistence/` implements the store with
  SQLAlchemy 2 async and an explicitly injected `async_sessionmaker` (no global
  session). `app.commands` knows nothing about SQLAlchemy, FastAPI or Agno, and
  `app.execution` does not depend on either.
- **Migrations.** The product schema is owned by Alembic (`alembic.ini`,
  `apps/api/migrations/`, revisions `0001` write commands and `0002` audit events),
  targets `APP_DATABASE_URL`, lives in
  the `product` schema (its version table too) and never touches `agno_runtime`.
  `create_all` is never used. API startup does not migrate: deployments run
  `uv run alembic upgrade head` explicitly. Each downgrade drops only its own table
  (`0002` → `product.audit_events`, `0001` → `product.write_commands`; never the
  schema, never CASCADE).
- **HTTP exposure.** Exactly one HTTP write uses it: `POST /api/v1/operations/tickets`
  (below), plus its read-only status read `GET /api/v1/operations/tickets/commands`.
  There is no generic command/action endpoint and no command listing;
  `POST /api/v1/operations/runs` stays read-only, and the Operations Agent is still not
  registered with AgentOS.

### Product ticket write: `POST /api/v1/operations/tickets` (durable, idempotent)

The first and only Product API write. It is **not an agent endpoint**: no model is
involved, and the caller explicitly asks for one ticket. The business action is
fixed server-side (`operations.ticket.create`); the client cannot name an action.

```
HTTP request
  → RequestContextMiddleware (server request id) → ActorResolver
  → trusted ActorContext                          (none → 401)
  → strict body {store_id, title, description}    (anything else → 422)
  → exact store grant: store_id ∈ actor.store_ids (otherwise → 403, nothing claimed)
  → trusted ActionScope(company_id=actor.company_id, store_id=<granted store>)
  → exactly one Idempotency-Key header            (missing → 400, invalid → 400)
  → OperationsTicketCommandService                (app.application adapter; none → 503)
  → WriteCommandCoordinator → PostgreSQL claim → GovernanceGate (tickets.create)
  → CreateOperationalTicketHandler → TicketingIntegration → verification → audit
  → durable command result
  → {request_id, command_id, status, reason, ticket_id, replayed, persistence_complete}
```

- **Authentication.** Product `ActorResolver` only (a Product API key; none → 401).
  `OS_SECURITY_KEY` is not Product authentication: it neither authenticates nor is
  needed here. The exact path is exempted from the AgentOS auth layer (no
  wildcard); every AgentOS route still requires the key.
- **Idempotency-Key is required.** It is opaque and case-sensitive, supplied by the
  client and passed through unchanged; the command layer validates it. Only its
  SHA-256 is stored; it is never logged, returned or put in audit data. Retrying
  the exact same request with the same key is always safe (it replays). The same key
  with a different title, description or store is `409 Idempotency conflict`, and
  nothing runs. Replays keep their original outcome (a DENIED command stays DENIED
  even after the actor is granted `tickets.create`; use a new key for a new attempt).
- **Outcome.** `status` is the business outcome; only `verified` means the ticket was
  created and independently confirmed, and only then is `ticket_id` (the canonical
  ticket UUID) set. `requires_human` (e.g. an uncertain provider write, or an outcome
  that could not be recorded) and `in_progress` are not success. HTTP codes report how
  the command was processed: 201 fresh VERIFIED; 200 replayed VERIFIED, DENIED,
  FAILED; 202 IN_PROGRESS, AWAITING_APPROVAL, REQUIRES_HUMAN or any result with
  `persistence_complete=false`. Governance DENIED is 200 with `status: denied`,
  not 403 (403 is only the store-scope rejection before any command exists).
- **Errors.** 400 key missing/invalid, 401 no actor, 403 store not granted, 409
  idempotency conflict, 422 malformed body, 503 service unconfigured, failing or
  returning an invalid result. Details are fixed strings; no key, fingerprint,
  parameters, SQL or provider data is ever returned, and every response carries
  `X-Request-ID` (which equals `request_id`).
- **No automatic recovery.** An `in_progress` command left by an interrupted process
  is safe but unresolved: retries replay it and never re-execute. Its durable state
  can be read with the status endpoint below (or by repeating the same POST).
- **Composition.** `create_app(..., operations_ticket_service=...)` takes the service
  from the caller (`WriteCommandTicketService(WriteCommandCoordinator(...))`); the app
  builds no store, coordinator, handler or integration and has no mock fallback.

### Ticket command status: `GET /api/v1/operations/tickets/commands?command_id=…` (read-only)

Reads the DURABLE state of one ticket command, by the `command_id` a POST returned,
without the original `Idempotency-Key`, without resubmitting anything, and without
ever executing, retrying or recovering anything.

```
HTTP request
  → RequestContextMiddleware (server request id) → ActorResolver
  → trusted ActorContext                          (none → 401)
  → command_id query parameter (UUID)             (malformed/missing → 422)
  → OperationsTicketCommandQueryService           (app.application adapter; none → 503)
  → WriteCommandReader.get_for_actor(command_id, actor.company_id, actor.actor_id)
      one SQL query scoped by command_id AND company_id AND actor_id
  → not found, another action, or store not CURRENTLY granted (exact) → 404
  → {request_id, command_id, status, reason, ticket_id, created_at, updated_at}
```

- **Fixed path.** The id is a query parameter, so the AgentOS auth exemption stays an
  exact path. The Product-authenticated paths are exactly `/api/v1/operations/runs`,
  `/api/v1/operations/tickets` and `/api/v1/operations/tickets/commands`;
  `OS_SECURITY_KEY` is not Product authentication, and AgentOS routes still require it.
- **Ownership in SQL.** The lookup is scoped to the original company and actor inside
  the query, so another principal's command is never even loaded. The actor's current
  store grant is then rechecked: a revoked store (or `*`, `all`, `stores.*`) hides the
  command. Unknown, another actor's, another company's, another action's and
  no-longer-granted commands all return the same `404 Ticket command not found`.
- **No write permission needed.** Reading the status of one's own command in a
  currently granted store performs no write, so `tickets.create` is not required.
- **What it returns.** `ticket_id` only for `verified`; `created_at`/`updated_at` are
  timezone-aware; `request_id` is this GET's id (equal to `X-Request-ID`), not the
  POST's. There is no `replayed` (a read is not a submission) and no
  `persistence_complete`: it is the stored truth.
- **Durable vs transient.** If a POST's terminal write failed, the POST answered
  `requires_human` / `command_persistence_incomplete` (`persistence_complete=false`)
  while the durable row stayed `in_progress`. A later GET reports `in_progress` with
  `reason: null`. This is intentional: the transient POST outcome is never
  reconstructed, and `command_persistence_incomplete` is never a durable state.
- **Errors.** 401 no actor, 404 not visible, 422 bad `command_id`, 503 query service
  unconfigured, failing or returning an invalid result; fixed messages only. An
  `Idempotency-Key` sent with a GET is ignored.
- **Composition.** `create_app(..., operations_ticket_query_service=...)` takes
  `WriteCommandTicketQueryService(<WriteCommandReader>)` from the caller (e.g. the
  `PostgresWriteCommandStore`); no approval, cancellation, listing or recovery exists.

### Daily operations report: `GET /api/v1/operations/reports/daily` (deterministic Workflow)

"Analyze operations today" is deterministic, so it is a **Workflow, not an Agent**:
backend code computes every number and finding; no model is called, and the LLM
never calculates metrics or discovers anomalies.

```
Product request -> Product auth -> exact store grant -> DailyOperationsReportService
  -> DailyOperationsWorkflow
       governance preflight: operations.store.read (stores.read),
         operations.orders.list (orders.read), operations.shipments.list
         (shipments.read) must ALL be allowed, else 403 before anything is read
       CommerceIntegration.get_store -> exact store + company, store.timezone (IANA)
       business day = [local midnight, next local midnight)   (DST-correct: 23/25 h)
       list_orders(store, created in the day) + list_shipments(store, shipped in the day)
       every record validated (store, window, parent order) -> any violation: 503
       canonical status counts + rule-based findings
  -> {request_id, report}
```

Query: `store_id` (UUID, required) and `business_date` (`YYYY-MM-DD`, optional; default:
"today" in the **store's** timezone). Nothing else is accepted (422): the caller never
supplies company, identity, timezone, permissions or report content.

- **Coverage** (machine-readable in every report): `orders: created_in_business_day`,
  `shipments: shipped_in_business_day`, `inventory: not_included` with
  `inventory_reason: store_scoped_inventory_query_unavailable` (the canonical contract
  has no safe store-to-inventory scoping yet, so inventory is NOT analyzed). It is not
  a history of every state change during the day: canonical models carry no event
  history.
- **Metrics** (over the complete result set): `orders_created`,
  `order_status_counts`, `shipments_shipped`, `shipment_status_counts` (every
  canonical status, zeros included) and `affected_orders` (distinct orders with a
  finding).
- **Findings** (exactly four rules; no SLA, "late", forecast or provider rule):
  order `unknown` -> `order_status_unknown` (warning, `review_status_mapping`);
  shipment `failed` -> `shipment_failed` (critical, `review_failed_shipment`);
  shipment `returned` -> `shipment_returned` (warning, `review_returned_shipment`);
  shipment `unknown` -> `shipment_status_unknown` (warning, `review_status_mapping`).
  Sorted by severity (critical first), code, order_id, entity_id; at most
  `MAX_DAILY_FINDINGS = 100` returned, with `findings_total` and `findings_truncated`.
- **Data minimization:** canonical store/order/shipment UUIDs and canonical statuses
  only; never provider statuses, external references, customer data, tracking
  numbers, company or actor identity.
- **Read-only:** no WriteCommand, ticket, ActionRun, audit lifecycle or agent run.
  `ShipmentQuery.store_id` (new) scopes shipments by their parent order's store; the
  adapter enforces it, and an unknown store returns no shipments.
- **Errors:** 401 without Product auth (the AgentOS key does not authenticate it),
  403 for an ungranted store or a missing report permission, 503 (fixed message) when
  the service is not composed or the integration, data or store timezone is unusable.
- **Composition:** with `APP_BUSINESS_BACKEND=mock` the workflow reads through the
  same `MockCommerceAdapter` and `GovernanceGate` as the Operations Agent; with
  `disabled` the route answers 503.

#### The Operations Agent consumes the report (Task 020)

```
User: "Analyze operations today"
  -> POST /api/v1/operations/runs (read-only Product run)
  -> Operations Agent -> get_daily_operations_report()           (no date: store-local today)
  -> DailyOperationsReportService (the SAME DailyOperationsWorkflow instance as the route)
  -> deterministic report -> the LLM explains it
```

- **The workflow calculates; the LLM explains.** Metrics and findings come from the
  backend workflow; the model does not calculate or recalculate them, add anomaly
  rules, or claim inventory analysis (coverage stays `not_included`).
- The tool's only model-visible argument is `business_date` (omit it for "today";
  otherwise exactly `YYYY-MM-DD`, else `invalid_date` without calling the service).
  Store, company, actor and timezone come only from the trusted run context. Results
  are `ok` (with the report) or `denied` / `invalid_date` / `unavailable` /
  `trusted_context_unavailable`, never exception text.
- **Findings are data, not authorization.** A critical finding never creates a
  ticket: `/runs` stays read-only (`create_operational_ticket` answers
  `action_not_requested`), and the programmatic write path is unchanged.
- `GET /api/v1/operations/reports/daily` remains the direct structured deterministic
  API; it never goes through the agent.

### Deployment composition: `app.bootstrap` and `APP_BUSINESS_BACKEND`

Two application factories, on purpose:

| Factory | Use | What it builds |
|---|---|---|
| `app.main:create_app` | LOW-LEVEL test/injection factory | FastAPI + AgentOS + Product auth; Product services only if the caller injects them. No persistence, integrations, coordinators or agents, and no business-backend policy. |
| `app.bootstrap:create_deployment_app` | DEPLOYMENT factory (operators) | Composes the Product services from settings (`app/composition/`), then calls `create_app`. |

```
Operator -> create_deployment_app() -> Settings -> deployment composition
  -> (mock only) one Product AsyncEngine -> one session factory
       -> PostgresWriteCommandStore + PostgresAuditSink
     ActionCatalog -> GovernanceGate -> ExecutionCoordinator(PostgresAuditSink)
     -> WriteCommandCoordinator -> ticket create + status services
     one MockCommerceSystem -> MockCommerceAdapter (reads) + MockTicketingAdapter (writes)
     Operations Agent -> OperationsAgentRunner (read-only Product boundary)
     DailyOperationsWorkflow (same adapter + gate) -> daily report service
  -> create_app(...) -> FastAPI + AgentOS (Product auth inside)
```

`APP_BUSINESS_BACKEND` (default `disabled`) chooses the business backend:

- **`disabled`**: local/test only. No Product business services are composed:
  `/operations/runs`, `/operations/tickets` and the status route answer 503. Useful for
  auth, AgentOS and infrastructure work without a model or backend.
- **`mock`**: local/test only. The real Product core (governance, execution,
  durable commands, durable audit, Product services, Operations Agent) on the
  deterministic in-memory mock commerce/ticketing backend. It requires an Operations
  model (`APP_DEFAULT_MODEL_PROVIDER`/`APP_DEFAULT_MODEL_ID`); without one composition
  fails (`Operations model is required for mock business composition`) instead of
  serving a permanently broken `/operations/runs`. Tests pass a deterministic scripted
  model explicitly; it is the only override `create_deployment_app` accepts.
- **staging/production**: the deployment factory REFUSES to start
  (`DeploymentCompositionError`) with either value. No authoritative real commerce
  backend exists yet, so there is no allowed backend; the mock is never constructed
  there, and there is no mock, dummy or in-memory fallback. This is intentional
  fail-closed behaviour; never use `mock` for a deployment.

The composition owns one Product engine, disposed exactly once when the application
shuts down (or immediately if the application cannot be built). Startup never
migrates: run `uv run alembic upgrade head` first. With `mock`, the ticket desk is in
memory: its tickets disappear on restart, while Product-owned command and audit state
stays in PostgreSQL (command status still answers from it). The Operations Agent is
still not registered with AgentOS, and `/operations/runs` stays read-only. Routes,
services, the domain, execution, governance and persistence never import
`app.composition`; mock integrations are imported only by
`app/composition/local_mock.py`.

## 3. Technology stack

| Concern | Choice |
|---|---|
| Backend | Python 3.13, FastAPI 0.141.1, Uvicorn 0.54.0 |
| Agent runtime | Agno 3.0.11 — AgentOS + PostgresDb (`agno[os,postgres,openai,anthropic]==3.0.11`) |
| Model providers | Agno native models: OpenAI Responses, Anthropic Claude (optional, `disabled` by default) |
| Python packaging | uv (`pyproject.toml`, `uv.lock`) |
| Config | pydantic-settings 2.15.0 |
| Database | PostgreSQL 17 + pgvector 0.8.6 (SQLAlchemy 2.1 async + psycopg 3) |
| Product migrations | Alembic 1.20.0 (`product` schema only) |
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
docker-compose.yml, pyproject.toml, uv.lock, .env.example, alembic.ini
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
| `APP_PRODUCT_AUTH_MODE` | API | `disabled` (local/test only) \| `api_key`. Staging/production require `api_key` |
| `APP_COMPANY_ID` | API | The single company of this deployment (required with `api_key`) |
| `APP_PRODUCT_API_KEYS` | API | JSON array of principals holding key SHA-256 hashes only (never raw keys) |
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
the API starts. The `product` schema is created only by the product migrations, which you run
explicitly (the API never migrates at startup):

```bash
set -a; . ./.env; set +a
uv run alembic upgrade head    # product schema + write_commands + audit_events
```

## 8. Run the API

Set `OS_SECURITY_KEY` in `.env` first (`openssl rand -hex 32`); the API refuses to start
without `APP_DATABASE_URL` or without a key of at least 32 characters. PostgreSQL must be running (section 7).

```bash
uv run uvicorn app.bootstrap:create_deployment_app --factory --app-dir apps/api --env-file .env --reload --port 8000
curl http://localhost:8000/health
```

This is the deployment factory (see "Deployment composition" above). With the default
`APP_BUSINESS_BACKEND=disabled` the Product business routes answer 503; set
`APP_BUSINESS_BACKEND=mock` (local/test only, needs a model provider) for the full
deterministic mock runtime. `app.main:create_app` remains the low-level injection
factory used by tests.

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
# With PostgreSQL running and migrated (section 7), run everything including integration tests:
set -a; . ./.env; set +a; uv run alembic upgrade head; uv run pytest
uv run ruff check . && uv run ruff format --check .
cd apps/web && npm run typecheck && npm run build
docker compose --env-file .env.example config --quiet   # validate compose
```

CI (`.github/workflows/ci.yml`) runs the backend, frontend and infrastructure checks above on
every pull request and on pushes to `main`. The backend job starts PostgreSQL via Compose,
runs the product migrations and proves they are reversible on the disposable database
(`alembic upgrade head`, `downgrade -1`, `upgrade head`), runs the integration tests (they must
not skip in CI), then boots the API with a CI-only
`OS_SECURITY_KEY` and checks `/health` and AgentOS authentication. The infrastructure job starts
PostgreSQL/Redis and verifies they are healthy and that pgvector is enabled.

**CI never calls a model provider.** No provider keys exist in CI and the default provider is
`disabled`. Agent execution is tested with a TEST-ONLY `DeterministicModel`
(`tests/support/deterministic_model.py`, an Agno `Model` returning a fixed response): the
`generic-reasoning` agent runs through the native `POST /agents/generic-reasoning/runs`
endpoint against PostgreSQL, with a guard that fails the test on any outbound connection,
and the run and session are read back from `agno_runtime`. Provider factory tests only
construct `OpenAIResponses`/`Claude` objects with dummy values; nothing is sent.
