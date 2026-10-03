# Deployment template (Docker Compose, v1)

The generic packaging template for ONE Product installation serving ONE company, with its
own PostgreSQL (never shared). It is **not** the local development stack (that is the
repository-root `docker-compose.yml`) and it is **not production-ready end to end yet**:
see [Current limitations](#current-limitations).

```
Browser
  -> http://127.0.0.1:${PRODUCT_WEB_PORT}   web: Operations Console + same-origin BFF
       (the ONLY host-published service)     (Web image, read-only root, UID 10002)
  -> private "product" network             -> api: Product API + AgentOS
       (PRODUCT_API_ORIGIN=http://api:8000)     (API image, read-only root, UID 10001,
                                                  NO host port)
  -> private "database" network            -> postgres (named volume, NO host port)

startup: postgres healthy -> migrate exits 0 -> api healthy (READY) -> web healthy (READY)
```

## 1. Build the two images

From the repository root (the build context MUST be the repository root for both):

```bash
docker build -f apps/api/Dockerfile -t commerce-ai-platform-api:0.1.0 .
docker build -f apps/web/Dockerfile -t commerce-ai-platform-web:0.1.0 .
```

Use explicit, locally controlled tags. No registry is used or published to. Both images
are independently reproducible and contain no secret.

### API image (`apps/api/Dockerfile`)

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
  (inside the container; the template does not publish it on the host at all).
- `HEALTHCHECK`: `GET /health/ready` via the Python standard library (no curl). Docker
  health is Product **readiness**: the lifespan started, the Agent runtime is attached,
  PostgreSQL is reachable and the Product schema is at the expected revision (Task 039).
- No secret is an `ARG` or `ENV` of the image. Everything sensitive is a runtime input.

Note: `opentelemetry-sdk` and the OTLP/HTTP exporter are Product runtime dependencies
(Task 039), used **only** when `APP_OTEL_EXPORT_MODE=otlp_http`; by default nothing is
exported. `pyyaml` is present because the pinned `agno[os]` depends on it.

### Web image (`apps/web/Dockerfile`)

- Multi-stage build (`dependencies` -> `builder` -> `runtime`) on
  `node:22.22.2-bookworm-slim`, pinned by manifest digest. Dependencies come only from
  `package-lock.json` (`npm ci`); no npm dependency was added for packaging. Next
  telemetry is disabled at build and run time.
- Next's **standalone** output (`output: "standalone"`): the runtime stage copies only
  `.next/standalone` (`server.js`, the compiled server code and the traced runtime
  `node_modules`) and `.next/static`. No source tree, tests, scripts, TypeScript or
  `@types` packages, `.git`, `.env*` or deployment material is in the image.
- Runs as the fixed unprivileged user **`web`, UID 10002 / GID 10002**; files are
  root-owned and read-only for it.
- Command `node server.js` (exec form, no entrypoint script: Node is PID 1 and receives
  SIGTERM directly; Next's own handler closes the server and exits 143).
- `HEALTHCHECK`: `GET /api/product/health/ready` with Node's built-in `fetch`, i.e.
  Next -> same-origin BFF -> private Product API `/health/ready`: the Web is healthy only
  while the whole installation is ready. No Product API key is involved.
- `PRODUCT_API_ORIGIN` is **not** baked into the image: it is deployment wiring, fixed by
  the template to `http://api:8000`. The Product API key is never on the server at all:
  it lives only in the browser tab's memory.

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
credentials; the Web service gets only `PRODUCT_API_ORIGIN=http://api:8000` (no database,
AgentOS, model or backend secret). `PRODUCT_API_ORIGIN` is deliberately not an operator
setting: the packaged BFF can only target the private API service.

## 3. Run

```bash
docker compose up -d        # postgres -> migrate (exits 0) -> api (ready) -> web (ready)
docker compose ps
```

Open the Operations Console at **http://127.0.0.1:3000** (or your `PRODUCT_WEB_PORT`) and
enter a Product API key and a Store UUID; the key stays in that browser tab's memory
only. The API is not reachable from the host, so do not try to browse it directly.

- **Migrations are explicit.** The `migrate` service runs `alembic upgrade head` with the
  same image and exits; the `api` service depends on it with
  `condition: service_completed_successfully`, and `web` depends on a healthy `api`, so
  **a failed migration keeps the API and the Web down**. The API never migrates itself. `docker compose up` re-runs the (idempotent)
  migration job before (re)starting the API. To run it on its own:
  `docker compose run --rm migrate`.
- **Read-only runtime.** `api`, `migrate` and `web` run with a read-only root
  filesystem, all capabilities dropped and `no-new-privileges`; only `/tmp` is a small
  tmpfs (Next's standalone server needs no other writable path).
- **Persistence.** PostgreSQL data lives in the named volume `postgres-data`. The
  template mounts `../../infra/postgres/init` (enables pgvector on first initialisation),
  so it is run from a checked-out Product repository.
- **Networks.** No host networking.
  - `database` (internal): PostgreSQL, the migration job and the API.
  - `product` (internal): the Web BFF and the API only.
  - `egress`: the API only, for outbound calls (e.g. a model provider).
  - `web-publish`: the Web only, to carry its localhost port. Outbound NAT is disabled
    on it (`enable_ip_masquerade: false`), so the Web gets no general Internet egress.
- **Exposure.** Only the Web is published, on `127.0.0.1:${PRODUCT_WEB_PORT}`. The API is not published on the host: the
  API, and the AgentOS routes inside it (`/agents`, `/info`, `/sessions`, …), are
  reachable only from the private `product` network, and the Web BFF forwards only its
  fixed Product routes. **AgentOS must never be directly internet-exposed**; remote
  or public access requires a later, reviewed TLS / reverse-proxy design (not part of
  this template).

Stop with `docker compose down` (keeps the database volume). `docker compose down -v`
**deletes the database**.

## Operations (Task 039)

See [`docs/PRODUCTION_OPERATIONS.md`](../../docs/PRODUCTION_OPERATIONS.md) for the details.

- **Health.** `GET /health/live` (process alive) and `GET /health/ready` (ready to serve)
  are public and answer only `{"status": ...}`. During a PostgreSQL outage the API stays
  alive (200) but not ready (503); it recovers by itself when PostgreSQL returns, with no
  restart. Detailed reasons are in the Product-authenticated **System Status**
  (`/api/v1/system/status`, permission `system.read`; the **System** page in the UI).
- **Logs.** The API writes one JSON completion record per Product operation to stdout,
  at `APP_LOG_LEVEL`. Logs stay deployment output (no log API).
- **Telemetry.** `APP_OTEL_EXPORT_MODE=disabled` (default) exports nothing.
  `otlp_http` sends the bounded Product traces and metrics to
  `APP_OTEL_EXPORT_ENDPOINT` (a collector base URL you run privately; `/v1/traces` and
  `/v1/metrics` are appended). No collector is part of this template, and no exporter
  header or credential is configured.
- **Backup.** `ops/backup.sh OUTPUT.dump` takes an ONLINE, full, custom-format dump of
  the installation database (0600, with a `.sha256` file, never overwriting one).
- **Restore.** `ops/restore-into-empty.sh BACKUP.dump` restores ONLY into an empty
  database with `api` and `web` stopped, verifies the checksum first, runs the Product
  migration, and does not start the Product.
- **Not in the database backup.** The deployment `.env`, `APP_INTEGRATION_SECRETS_DIR`,
  `APP_BACKEND_SECRETS_DIR` and `APP_BACKEND_CONFIG_DIR` are separate recovery assets:
  protect them separately.

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
- scheduled or off-site backups: `ops/backup.sh` and `ops/restore-into-empty.sh`
  exist, but **scheduling, retention and off-site copies are your own policy**;
- image registry publication and release automation;
- container orchestrators (Kubernetes, Helm, Nomad, Terraform, Ansible);
- login, accounts or server-side sessions (the Product API key is entered per browser
  tab and kept in memory only).
