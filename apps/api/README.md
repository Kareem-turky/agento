# apps/api

The Product API (FastAPI) with the Agno AgentOS runtime attached. Import package: `app`.

- `app/config.py` — product settings (`APP_*` environment variables, optional `.env`).
- `app/main.py` — `create_app()` factory and the Product `GET /health` route; accepts an
  optional `operations_service`, `operations_ticket_service` and
  `operations_ticket_query_service` (no defaults and no mock fallback).
- `app/routes/operations.py` — `POST /api/v1/operations/runs`, the read-only,
  `ActorResolver`-authenticated Operations Agent route (never writes).
- `app/services/operations.py` — the runtime-independent `OperationsRunService`
  contract and `ProductOperationsRunResult`.
- `app/routes/operations_tickets.py` — `POST /api/v1/operations/tickets`, the only
  Product API write: a deterministic, `ActorResolver`-authenticated, idempotent
  (`Idempotency-Key`) ticket command. No agent or model involved. Also
  `GET /api/v1/operations/tickets/commands?command_id=…`, its read-only durable status.
- `app/services/operations_tickets.py` — the infrastructure-independent
  `OperationsTicketCommandService` and `OperationsTicketCommandQueryService` contracts,
  `ProductTicketCommandResult`, `ProductTicketCommandStatusResult` and safe errors.
- `app/application/operations_tickets.py` — `WriteCommandTicketService`: implements the
  ticket contract on an injected `WriteCommandCoordinator` (action fixed server-side).
- `app/application/operations_ticket_queries.py` — `WriteCommandTicketQueryService`:
  the read-only ticket command status, on an injected principal-scoped
  `WriteCommandReader` (ownership in SQL, current store grant rechecked).
- `app/runtime/agentos.py` — attaches `AgentOS(base_app=...)` with a native `PostgresDb`
  (schema `agno_runtime`) and the agents from `components.py`. Requires `APP_DATABASE_URL`
  and `OS_SECURITY_KEY`.
- `app/runtime/models.py` — maps `APP_DEFAULT_MODEL_PROVIDER`/`APP_DEFAULT_MODEL_ID` to a
  native Agno model (`OpenAIResponses`, `Claude`) or `None` when disabled.
- `app/runtime/components.py` — decides which agents AgentOS registers per environment.
- `app/agents/generic_reasoning.py` — the `generic-reasoning` agent (takes any Agno `Model`).
- `app/agents/operations*.py` — the Operations Agent, its four governed product tools
  (`get_order`, `get_order_shipments`, `get_daily_operations_report`,
  `create_operational_ticket`) and `OperationsAgentRunner`. For daily/store-wide
  analysis it calls `get_daily_operations_report`, which only uses the injected
  `DailyOperationsReportService`: the deterministic workflow calculates, the LLM
  explains (no recalculation, no invented findings, inventory stays `not_included`;
  a critical finding never creates a ticket). Not registered with AgentOS; its only
  HTTP entry point is the read-only `POST /api/v1/operations/runs`.
- `app/auth/` — Product authentication: `ProductApiKeyActorResolver` (`Authorization:
  Bearer <Product API key>` → SHA-256 → constant-time match → `ActorContext` of the
  deployment's single company) and `build_actor_resolver(settings)`, the default used by
  `create_app` (fails closed in staging/production without Product auth). Operator helper:
  `apps/api/scripts/hash_product_api_key.py` (reads the key from stdin, prints its SHA-256).
- `app/context/` — immutable `ActorContext`/`RequestContext`, the `ActorResolver`
  boundary (`NoActorResolver` when auth is disabled, local/test only), the per-request middleware (`X-Request-ID`) and
  the `get_request_context` / `require_actor_context` dependencies.
- `app/commerce/domain/` — canonical, provider-independent commerce models (no
  persistence, APIs or adapters yet; depends only on Pydantic and the standard library).
- `app/company/operating_model/` — immutable company configuration (SLAs, escalation
  rules, KPIs, reporting, capabilities); pure config, nothing is evaluated or loaded yet.
- `app/integrations/http/` — the Product-owned secure outbound HTTP transport for future
  provider adapters (`IntegrationHttpTransport`, `HttpxIntegrationTransport`,
  `IntegrationHttpPolicy`). It has one trusted HTTPS origin and relative paths only.
  TLS verification is on, and redirects, environment proxies and cookies are disabled.
  Timeouts, connection limits and request and decoded-response size caps are explicit.
  Only GET/HEAD are retried (bounded, deterministic); writes are sent exactly once.
  Errors are fixed and carry `request_may_have_been_sent`, and a failed `close()` is a
  fixed `IntegrationHttpCloseError`. It does no logging. httpx/httpcore records emitted
  during its requests are dropped by a per-task ContextVar filter
  (`dependency_logging.py`). Nothing
  in the Product imports it yet, and agents, workflows and models never receive it.
- `app/integrations/commerce/` — product-owned async `CommerceIntegration` contract
  (read-only), the `TicketingIntegration` write contract, queries, capabilities and
  errors; `mock/` holds a deterministic in-memory mock provider, its adapter and the
  mock ticket desk and ticketing adapter (development/tests only). `CommerceIntegration`
  is the provider-independent boundary: every adapter must pass the offline
  conformance harness in `tests/commerce_conformance/` (canonical identity, query
  semantics, deterministic sorting, half-open windows, store isolation, error
  translation, data minimization, read-only capabilities) before it may be composed;
  passing is necessary, not sufficient, for production (see the root README, "Adding a
  commerce adapter").
- `app/governance/` — action catalog, untrusted action intents, permission evaluation
  and the baseline policy (ALLOW / DENY / REQUIRE_APPROVAL); decisions only, nothing
  is executed.
- `app/execution/` — governed execution coordinator (govern → validate → execute →
  verify → audit), handler protocol and immutable registry, `ActionRun` statuses and
  metadata-only audit events; no real actions or persistence.
- `app/operations/` — product business actions on the governed path; today only
  `operations.ticket.create` (`CreateOperationalTicketHandler`, via the
  `TicketingIntegration` contract), plus the `operations.order.read` and
  `operations.shipments.read` read actions. Reached only by the Operations Agent.
- `app/commands/` — durable write commands: `WriteCommandCoordinator` (idempotent,
  above `ExecutionCoordinator`), the `WriteCommandStore` contract, key/fingerprint
  hashing and command models. No SQLAlchemy, FastAPI or Agno. Reached over HTTP only
  through the ticket service contract.
- `app/persistence/` — SQLAlchemy 2 async infrastructure: explicit engine/session
  factories, `PostgresWriteCommandStore` (`product.write_commands`; also the
  principal-scoped `WriteCommandReader`) and `PostgresAuditSink`
  (`product.audit_events`: metadata-only, append-only, one short INSERT+COMMIT
  transaction per event, committed before `record` returns, so `execution_started` is
  durable before any side effect; failures raise the generic `AuditPersistenceError`
  and the coordinator then refuses to execute or to report `verified`). Audit rows are
  separate from command rows. No audit read API or route; not wired into `create_app`.
- `migrations/` — Alembic migrations for the `product` schema (config: `/alembic.ini`,
  target `APP_DATABASE_URL`): `0001` write commands, `0002` audit events. Run
  explicitly: `uv run alembic upgrade head`; the API never migrates at startup.
- `app/runtime/non_executing_model.py` — placeholder model so the smoke agent needs no
  model provider; it raises if invoked.

- `app/observability/` — Product observability (not audit): the Product-owned
  contract (`ProductOperation`, `ObservationOutcome`, bounded details, the shielding
  `observe` helper), the default implementation (`otel.py`: OpenTelemetry API spans,
  `product.operation.count`/`product.operation.duration` metrics, and one JSON
  completion log per operation on `app.product.observability`), the streaming-safe
  `ProductObservabilityMiddleware` (the five Product paths only, inside
  `RequestContextMiddleware`) and the observed service decorators applied by
  `create_app(observability=...)`. The only package that imports OpenTelemetry; no
  exporter, network, thread or database; never business payloads, identifiers or
  exception text; failures never affect the Product.

- `app/workflows/` — deterministic Product workflows (no model): the
  `DailyOperationsWorkflow` behind `GET /api/v1/operations/reports/daily`
  (governance preflight for `stores.read`/`orders.read`/`shipments.read`, store-local
  business day, orders created and shipments shipped that day, four canonical-status
  finding rules, inventory explicitly `not_included`). Depends on contracts and the
  canonical domain only; the composition root wires it to a concrete integration.
- `app/composition/` — deployment composition root: `APP_BUSINESS_BACKEND` is a
  Product-owned backend plugin id (syntax-checked in settings, never a module path)
  resolved against the immutable allowlist in `registry.py` (built-ins: the `disabled`
  sentinel and the `mock` plugin); `deployment.py` selects generically (resolve ->
  allowed environment -> registered builder) and has no backend-specific branch.
  Future backends must pass the conformance harness and review, then be added as one
  explicit registration. `mock` (local/test only)
  wires one Product engine, `PostgresWriteCommandStore`, `PostgresAuditSink`,
  governance, `ExecutionCoordinator`, `WriteCommandCoordinator`, the ticket services
  and the Operations Agent runner onto ONE deterministic `MockCommerceSystem`
  (`local_mock.py`, the only module importing the mock integration). Staging and
  production are refused: no real business backend exists yet (fail closed).
- `app/bootstrap.py` — `create_deployment_app`, the operator entry point; disposes the
  composed engine on shutdown. Never migrates.
- `app/main.py` — `create_app`, the LOW-LEVEL injection factory (tests, explicit
  compositions); builds no persistence, integrations or agents.

Run (deployment factory):
`uv run uvicorn app.bootstrap:create_deployment_app --factory --app-dir apps/api --env-file .env`

Product endpoints: `GET /health`, `POST /api/v1/operations/runs` (read-only agent run),
`GET /api/v1/operations/reports/daily` (deterministic daily report, no model),
`POST /api/v1/operations/tickets` (the only write) and
`GET /api/v1/operations/tickets/commands` (its read-only status).
