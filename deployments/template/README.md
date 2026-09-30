# Deployment template (Docker Compose, v1)

The generic packaging template for ONE Product installation serving ONE company, with its
own PostgreSQL (never shared). It is **not** the local development stack (that is the
repository-root `docker-compose.yml`) and it is **not production-ready end to end yet**:
see [Current limitations](#current-limitations).

```
immutable API image (apps/api/Dockerfile)
  postgres (healthy, private network, named volume, no host port)
    -> migrate  (same image: alembic upgrade head, then exits 0)
      -> api    (same image: app.bootstrap deployment factory, read-only root,
                 non-root, published on 127.0.0.1 only)
        -> future: reviewed reverse proxy / TLS / Product UI (not implemented)
```

## 1. Build the API image

From the repository root (the build context MUST be the repository root):

```bash
docker build -f apps/api/Dockerfile -t commerce-ai-platform-api:0.1.0 .
```

Use an explicit, locally controlled tag. No registry is used or published to.

- Multi-stage build on `python:3.13.12-slim-bookworm`, pinned by manifest digest (the
  same Python 3.13 as `.python-version`). uv `0.8.17` (same as CI, hash-verified) installs
  exactly `uv.lock` with `uv sync --locked --no-dev`: no test or lint tools. uv itself is
  not in the runtime image.
- The runtime stage copies an explicit whitelist only: the virtual environment,
  `alembic.ini`, `apps/api/app/` and `apps/api/migrations/`. Tests, `.git`, `.env*`, the
  web app and deployment material are never copied (`.dockerignore` excludes them from
  the build context as well).
- Runs as the fixed unprivileged user **`app`, UID 10001 / GID 10001**. Application files
  are root-owned and read-only for it; nothing in the image is world-writable.
- Default command (exec form, uvicorn is PID 1 and receives SIGTERM directly; no
  `--reload`, no migration):
  `python -m uvicorn app.bootstrap:create_deployment_app --factory --app-dir /app/apps/api --host 0.0.0.0 --port 8000`
  (inside the container; the template publishes it on `127.0.0.1` only).
- `HEALTHCHECK`: `GET /health` via the Python standard library (no curl).
- No secret is an `ARG` or `ENV` of the image. Everything sensitive is a runtime input.

Note: `opentelemetry-sdk` and `pyyaml` are present in the runtime image because the pinned
`agno[os]` depends on them (through `openinference-instrumentation-agno`). The Product
configures no SDK and no exporter (see the root README, "Product observability").

## 2. Configure

```bash
cd deployments/template
cp .env.example .env
chmod 600 .env
```

Fill in `.env` (never commit it; `.env` files are git-ignored):

- `POSTGRES_PASSWORD`: a URL-safe random value (`openssl rand -hex 32`); it is embedded in
  the database URL the API and the migration job use.
- `OS_SECURITY_KEY`: at least 32 characters (`openssl rand -hex 32`). It protects the
  AgentOS runtime routes.
- `APP_COMPANY_ID` and `APP_PRODUCT_API_KEYS` (SHA-256 hashes only, never raw keys):
  Product API authentication is mandatory in staging/production.
- Model provider keys only if a provider is selected.

`docker compose config` refuses to render while a required secret is missing.

Each service receives only what it needs: the migration job gets only the database URL
(no AgentOS key, no model keys, no backend inputs); PostgreSQL gets only its own
credentials.

## 3. Run

```bash
docker compose up -d        # postgres healthy -> migrate exits 0 -> api starts
docker compose ps
curl -s http://127.0.0.1:8000/health
```

- **Migrations are explicit.** The `migrate` service runs `alembic upgrade head` with the
  same image and exits; the `api` service depends on it with
  `condition: service_completed_successfully`, so **a failed migration keeps the API
  down**. The API never migrates itself. `docker compose up` re-runs the (idempotent)
  migration job before (re)starting the API. To run it on its own:
  `docker compose run --rm migrate`.
- **Read-only runtime.** `api` and `migrate` run with a read-only root filesystem, all
  capabilities dropped and `no-new-privileges`; only `/tmp` is a small tmpfs.
- **Persistence.** PostgreSQL data lives in the named volume `postgres-data`. The
  template mounts `../../infra/postgres/init` (enables pgvector on first initialisation),
  so it is run from a checked-out Product repository.
- **Networks.** `database` is internal (no external connectivity): PostgreSQL, the
  migration job and the API. Only the API also joins `egress`, for its localhost port
  and outbound calls (e.g. a model provider). No host networking.
- **Exposure.** PostgreSQL publishes no host port. The API is published on
  `127.0.0.1:${PRODUCT_API_PORT}` only. **AgentOS routes (`/agents`, `/info`, …) must never
  be directly internet-exposed**; remote access requires a reviewed reverse proxy that
  exposes Product routes deliberately (not part of this template yet).

Stop with `docker compose down` (keeps the database volume). `docker compose down -v`
**deletes the database**.

## Business backend and its inputs

**No real business backend exists yet.** With `APP_ENVIRONMENT=production` (or staging)
the API intentionally refuses to start: `APP_BUSINESS_BACKEND=disabled` fails closed
("no business backend is available for staging/production deployments") and `mock` is
refused ("selected business backend is not allowed in this environment"). **`mock` is
forbidden for a real deployment.** This template therefore does not yet produce a
working production installation; it packages the runtime for when a reviewed backend
is registered.

A future real backend declares the NAMES of its config and secret inputs (Task 025).
The operator will then mount a config directory and a secrets directory **read-only**
into the API container (a compose override file kept outside git) and set
`APP_BACKEND_CONFIG_DIR` / `APP_BACKEND_SECRETS_DIR` to those container paths. Input
files are single regular files named exactly like the declared input (no symlinks, so
use direct file or `subPath`-style mounts). Backend credentials never belong in git,
in an image, in `.env.example`, or in prompts.

## Secrets

- Nothing sensitive is committed or baked into the image: database password, database
  URL, `OS_SECURITY_KEY`, Product API keys, model keys and backend secrets are supplied at
  container runtime only.
- Keep `.env` outside version control and readable only by the operator account.

## Current limitations

Not implemented yet (later, explicit tasks):

- a real business backend / provider installation (production cannot start);
- reverse proxy, public ingress and TLS termination;
- backup and restore automation: **the `postgres-data` volume needs your own backup
  policy** until then;
- image registry publication and release automation;
- container orchestrators (Kubernetes, Helm, Nomad, Terraform, Ansible);
- a Product Web UI container.
