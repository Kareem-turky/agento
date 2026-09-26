# apps/api

The Product API (FastAPI) with the Agno AgentOS runtime attached. Import package: `app`.

- `app/config.py` — product settings (`APP_*` environment variables, optional `.env`).
- `app/main.py` — `create_app()` factory and the Product `GET /health` route.
- `app/runtime/agentos.py` — attaches `AgentOS(base_app=...)` with a native `PostgresDb`
  (schema `agno_runtime`) and the agents from `components.py`. Requires `APP_DATABASE_URL`
  and `OS_SECURITY_KEY`.
- `app/runtime/models.py` — maps `APP_DEFAULT_MODEL_PROVIDER`/`APP_DEFAULT_MODEL_ID` to a
  native Agno model (`OpenAIResponses`, `Claude`) or `None` when disabled.
- `app/runtime/components.py` — decides which agents AgentOS registers per environment.
- `app/agents/generic_reasoning.py` — the `generic-reasoning` agent (takes any Agno `Model`).
- `app/context/` — immutable `ActorContext`/`RequestContext`, the `ActorResolver`
  boundary (default `NoActorResolver`), the per-request middleware (`X-Request-ID`) and
  the `get_request_context` / `require_actor_context` dependencies.
- `app/commerce/domain/` — canonical, provider-independent commerce models (no
  persistence, APIs or adapters yet; depends only on Pydantic and the standard library).
- `app/company/operating_model/` — immutable company configuration (SLAs, escalation
  rules, KPIs, reporting, capabilities); pure config, nothing is evaluated or loaded yet.
- `app/runtime/non_executing_model.py` — placeholder model so the smoke agent needs no
  model provider; it raises if invoked.

Run: `uv run uvicorn app.main:create_app --factory --app-dir apps/api --env-file .env`

No business endpoints live here yet.
