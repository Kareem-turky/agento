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
- **Scope:** `CommerceIntegration` is read-only: no tools, agents, policy, webhooks or
  persistence. No real provider (for example Shopify or WooCommerce) is implemented.
  The only write is the separate `TicketingIntegration` contract, reached through
  the governed `operations.ticket.create` action (see below).

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
- **Configuration:** only the three tools, `tool_call_limit=6`, `telemetry=False`,
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
  product `ActorResolver` only. With the default `NoActorResolver` it returns 401,
  even with a valid AgentOS key. With a trusted resolver it needs no AgentOS key.
  Its exact path is exempted from the AgentOS auth layer (Agno's
  `AuthorizationConfig.excluded_route_paths`; exact paths only, no wildcards), and
  every AgentOS route still requires the key. No credential provider is added here:
  a concrete deployment authentication adapter plugs into `ActorResolver` later.
- **Store and company.** `store_id` is a client-selected target. It becomes trusted
  scope only after an exact membership check against `actor.store_ids` (no
  wildcards: `*`, `all` or an empty set grant nothing). A store not granted,
  existing or not, is a generic 403 and the agent never runs. `company_id` always
  comes from `ActorContext.company_id`; the request has no company field. The body
  and prompt cannot supply identity, permissions or write intent.
- **Read-only.** `run_product` always runs with `requested_write_actions` empty. The
  ticket write still exists internally (programmatic `OperationsAgentRunner.run`)
  but is not exposed over HTTP: even a model that calls `create_operational_ticket`
  gets `action_not_requested`, and `ExecutionCoordinator` is never entered. HTTP
  writes need a durable write-command and idempotency boundary first, which does
  not exist yet.
- **Tool permissions still apply.** Store access only selects the target;
  `orders.read` and `shipments.read` are still enforced by the governed tools.
- **Safe results and errors.** Only the final assistant text is returned, never the
  Agno `RunOutput`, tool calls, context or policy data. That text is display text,
  never an authorization decision. With no service configured the route returns
  503; there is no mock fallback. A failed run (for example a model error) or a
  service exception also gives a generic 503 with no internal detail, and every
  response carries `X-Request-ID`.
- **Not included:** sessions, chat history, streaming, a frontend, persistence or
  an idempotency cache.

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
