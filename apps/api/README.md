# apps/api

The Product API (FastAPI) with the Agno AgentOS runtime attached. Import package: `app`.

- `app/config.py` — product settings (`APP_*` environment variables, optional `.env`).
- `app/main.py` — `create_app()` factory and the Product `GET /health` route; accepts an
  optional `operations_service` and `operations_ticket_service` (no defaults and no mock
  fallback).
- `app/routes/operations.py` — `POST /api/v1/operations/runs`, the read-only,
  `ActorResolver`-authenticated Operations Agent route (never writes).
- `app/services/operations.py` — the runtime-independent `OperationsRunService`
  contract and `ProductOperationsRunResult`.
- `app/routes/operations_tickets.py` — `POST /api/v1/operations/tickets`, the only
  Product API write: a deterministic, `ActorResolver`-authenticated, idempotent
  (`Idempotency-Key`) ticket command. No agent or model involved.
- `app/services/operations_tickets.py` — the infrastructure-independent
  `OperationsTicketCommandService` contract, `ProductTicketCommandResult` and safe errors.
- `app/application/operations_tickets.py` — `WriteCommandTicketService`: implements the
  ticket contract on an injected `WriteCommandCoordinator` (action fixed server-side).
- `app/runtime/agentos.py` — attaches `AgentOS(base_app=...)` with a native `PostgresDb`
  (schema `agno_runtime`) and the agents from `components.py`. Requires `APP_DATABASE_URL`
  and `OS_SECURITY_KEY`.
- `app/runtime/models.py` — maps `APP_DEFAULT_MODEL_PROVIDER`/`APP_DEFAULT_MODEL_ID` to a
  native Agno model (`OpenAIResponses`, `Claude`) or `None` when disabled.
- `app/runtime/components.py` — decides which agents AgentOS registers per environment.
- `app/agents/generic_reasoning.py` — the `generic-reasoning` agent (takes any Agno `Model`).
- `app/agents/operations*.py` — the Operations Agent, its governed product tools and
  `OperationsAgentRunner`. Not registered with AgentOS or exposed over HTTP yet.
- `app/context/` — immutable `ActorContext`/`RequestContext`, the `ActorResolver`
  boundary (default `NoActorResolver`), the per-request middleware (`X-Request-ID`) and
  the `get_request_context` / `require_actor_context` dependencies.
- `app/commerce/domain/` — canonical, provider-independent commerce models (no
  persistence, APIs or adapters yet; depends only on Pydantic and the standard library).
- `app/company/operating_model/` — immutable company configuration (SLAs, escalation
  rules, KPIs, reporting, capabilities); pure config, nothing is evaluated or loaded yet.
- `app/integrations/commerce/` — product-owned async `CommerceIntegration` contract
  (read-only), the `TicketingIntegration` write contract, queries, capabilities and
  errors; `mock/` holds a deterministic in-memory mock provider, its adapter and the
  mock ticket desk and ticketing adapter (development/tests only).
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
  factories and `PostgresWriteCommandStore` (`product.write_commands`).
- `migrations/` — Alembic migrations for the `product` schema (config: `/alembic.ini`,
  target `APP_DATABASE_URL`). Run explicitly: `uv run alembic upgrade head`; the API
  never migrates at startup.
- `app/runtime/non_executing_model.py` — placeholder model so the smoke agent needs no
  model provider; it raises if invoked.

Run: `uv run uvicorn app.main:create_app --factory --app-dir apps/api --env-file .env`

Product endpoints: `GET /health`, `POST /api/v1/operations/runs` (read-only agent run) and
`POST /api/v1/operations/tickets` (the only write).
