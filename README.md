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
            ┌──────────────┐        ┌──────────────────────────────┐
  users ──▶ │  apps/web    │ ─────▶ │  apps/api (FastAPI)           │
            │  (Next.js)   │  HTTP  │   ├─ config (pydantic-settings)│
            └──────────────┘        │   └─ runtime ─▶ Agno Registry  │
                                    └──────┬───────────────┬────────┘
                                           │               │
                          core/ · commerce/ · intelligence/   (future product layers)
                                           │
                          integrations/contracts ◀─ integrations/adapters ─▶ external systems
                                           │
                               PostgreSQL + pgvector      Redis
```

Today only `apps/api` (with `/health` and Agno runtime registration), `apps/web` (placeholder
page) and local PostgreSQL/Redis infrastructure are implemented.

## 3. Technology stack

| Concern | Choice |
|---|---|
| Backend | Python 3.13, FastAPI 0.141.1, Uvicorn 0.54.0 |
| Agent runtime | Agno 3.0.11 (external dependency) |
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
  api/            FastAPI application (import package `app`)
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
| `APP_DATABASE_URL` | API | Optional PostgreSQL DSN |
| `APP_REDIS_URL` | API | Optional Redis DSN |
| `AGNO_TELEMETRY` | Agno | Set `false` to disable Agno telemetry |
| `NEXT_PUBLIC_API_BASE_URL` | web | API base URL (reserved for later use) |

## 7. Start PostgreSQL / Redis

```bash
docker compose up -d
docker compose ps             # both services should become "healthy"
docker compose down           # stop (add -v to delete volumes)
```

The pgvector extension is enabled on first volume initialisation
(`infra/postgres/init/01-extensions.sql`). No application schemas are created.

## 8. Run the API

```bash
uv run uvicorn app.main:app --app-dir apps/api --reload --port 8000
curl http://localhost:8000/health
```

## 9. Run the frontend

```bash
cd apps/web
npm run dev                   # http://localhost:3000
```

## 10. Run tests

```bash
uv run pytest
uv run ruff check . && uv run ruff format --check .
cd apps/web && npm run typecheck && npm run build
docker compose --env-file .env.example config --quiet   # validate compose
```

CI (`.github/workflows/ci.yml`) runs the backend, frontend and infrastructure checks above on
every pull request and on pushes to `main`. It also starts PostgreSQL/Redis and verifies they
are healthy and that pgvector is enabled.
