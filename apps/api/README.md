# apps/api

The Product API (FastAPI) with the Agno AgentOS runtime attached. Import package: `app`.

- `app/config.py` — product settings (`APP_*` environment variables, optional `.env`).
- `app/main.py` — `create_app()` factory and the Product `GET /health` route.
- `app/runtime/agentos.py` — attaches `AgentOS(base_app=...)` with a native `PostgresDb`
  (schema `agno_runtime`) and the `runtime-smoke-test` agent. Requires `APP_DATABASE_URL`
  and `OS_SECURITY_KEY`.
- `app/runtime/non_executing_model.py` — placeholder model so the smoke agent needs no
  model provider; it raises if invoked.

Run: `uv run uvicorn app.main:create_app --factory --app-dir apps/api --env-file .env`

No business endpoints live here yet.
