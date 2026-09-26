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

**Smoke-test agent.** One non-production agent, `runtime-smoke-test`, proves component
registration. It has no tools and no memory, and uses `NonExecutingModel` — a placeholder
that satisfies Agno's `Model` interface and raises if invoked — so no model provider SDK or
API key is needed (AgentOS would otherwise default to OpenAI). It is never executed.

**Security.** The AgentOS routes are protected by Agno's `OS_SECURITY_KEY` bearer-key
mechanism; the app refuses to start without it. `GET /health` stays public for
infrastructure monitoring. Agno also leaves `/`, `/info`, `/docs`, `/redoc` and
`/openapi.json` public. This key is a **temporary runtime guard**, not product
authentication: user identity, roles and authorization will come in later tasks.

**AgentOS is an internal runtime/API surface.** The frontend must not be designed to depend
directly on AgentOS APIs. Product-facing APIs will sit above the runtime where appropriate.

## 3. Technology stack

| Concern | Choice |
|---|---|
| Backend | Python 3.13, FastAPI 0.141.1, Uvicorn 0.54.0 |
| Agent runtime | Agno 3.0.11 — AgentOS + PostgresDb (`agno[os,postgres]==3.0.11`) |
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
commerce/         generic commerce domain — placeholder
integrations/
  contracts/      system-neutral interfaces — placeholder
  adapters/       external-system implementations — placeholder
company/          per-installation config, policies, operating model — placeholder
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
| `OS_SECURITY_KEY` | Agno | **Required.** Bearer key for AgentOS routes (read by Agno) |
| `AGNO_TELEMETRY` | Agno | Set `false` to disable Agno telemetry |
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

Set `OS_SECURITY_KEY` in `.env` first (e.g. `openssl rand -hex 32`); the API refuses to
start without it or without `APP_DATABASE_URL`. PostgreSQL must be running (section 7).

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

Open <http://localhost:8000/docs> (Swagger UI) or <http://localhost:8000/redoc>. Product and
AgentOS routes appear together. Use **Authorize** with the security key to call AgentOS routes.

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
