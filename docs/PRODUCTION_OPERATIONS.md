# Production operations

Task 039 makes an Agento installation operationally inspectable and recoverable. It adds:

- liveness and readiness;
- a Product-authenticated System Status;
- structured logs at a configured level;
- optional OpenTelemetry export;
- health checks that mean "ready";
- operator backup and restore-into-empty tooling.

It changes no business-domain semantics. It adds no migration (head stays `0008`), no
monitoring service, no metrics or log API, no scheduler and no public exposure.

## Health model

Three questions are kept separate:

| Question | Endpoint | Who | Answer |
| --- | --- | --- | --- |
| Is the process alive? | `GET /health/live` | public | `200 {"status":"alive"}`. No dependency is touched. |
| Can this instance serve Product work? | `GET /health/ready` | public | `200 {"status":"ready"}` or `503 {"status":"not_ready"}`. Never a reason. |
| Why (not), and what is up? | `GET /api/v1/system/status` | `system.read` | Components, stable reasons, version, environment, uptime, telemetry export mode. |

`GET /health` is unchanged and backward-compatible. It is the existing compatibility
response, used by the browser's API reachability indicator. Deployment health decisions
use readiness instead.

### Readiness

An instance is ready only when ALL of these hold:

1. the application lifespan has started;
2. the Agent runtime is attached;
3. PostgreSQL is reachable;
4. the Product schema revision is exactly the expected head (`0008`).

The expected head is stated once, in `app/system_operations/revision.py`. A test pins it
to the Alembic head.

The database probe (`app/persistence/system_readiness.py`) works like this on every
call:

- It opens a fresh, pool-less connection to `APP_DATABASE_URL`.
- It runs `SELECT 1`, then reads `product.alembic_version`.
- The whole check is bounded at 2 seconds (`asyncio.timeout` plus the connect timeout)
  and is never retried within one request.
- A missing, unreadable, different or multi-row revision is a schema mismatch.
- Every failure maps to a fixed state. No URL, host, port, SQL or driver text is ever
  returned, stored or logged.

Readiness is evaluated fresh on every request. When PostgreSQL goes away the same
process reports `not_ready`. When PostgreSQL returns, the same process reports `ready`
again, with no restart.

Readiness deliberately does not depend on any of these:

- the Operations Agent, a model or an integration;
- connection tests;
- conversations, approvals, or which Agents are enabled;
- Redis;
- the telemetry collector.

Business features never make an instance unready.

### System Status

`GET /api/v1/system/status` uses normal Product authentication. The AgentOS key is
rejected, and the caller needs `system.read`, the only System permission. There is no
write, restart, backup or restore permission or route.

The response contains only:

- the Agento version, the environment and the process uptime;
- `overall`: `ready` or `not_ready`;
- `reasons`, stable codes from:
  - `application_starting`
  - `agent_runtime_starting`
  - `database_unavailable`
  - `schema_mismatch`
  - `schema_unavailable`
- component states (`ready`, `starting`, `unavailable`, `mismatch`) for:
  - `application`
  - `database`
  - `product_schema`
  - `agent_runtime`
- the telemetry `export_mode` (`disabled` or `otlp_http`), never the endpoint.

It never contains any of these:

- a database URL, host, IP, database name or user;
- a company, store, actor or key identifier or hash;
- the AgentOS key, a model ID or key;
- backend or integration configuration or secrets;
- an OTel endpoint, a filesystem path or exception text.

The Control Center shows it read-only on **System** (`/system`, under Configure). It
uses the shared in-memory Product session, has no polling (Refresh is explicit), and
shows fixed messages:

- 403: "You don't have access to System status."
- 503: "System status is unavailable right now."
- 401: marks the key rejected.

## Docker health

- **API:** `HEALTHCHECK` calls `GET /health/ready`. Docker health therefore means the
  Product is ready, not merely that the process exists.
- **Web:** `HEALTHCHECK` calls the fixed BFF route `GET /api/product/health/ready`, which
  goes to the API's `/health/ready`. The Web is healthy only while the whole
  installation is ready. That BFF route is public and minimal: no Product API key is
  involved.
- **Startup order (unchanged):** PostgreSQL healthy, then the migration job exits 0, then
  the API is healthy (ready), then the Web is healthy (ready). A failed migration still
  keeps the API and the Web down.
- **PostgreSQL outage:**
  - `/health/live` stays 200 and `/health/ready` becomes 503.
  - The BFF readiness and the Web health check fail.
  - System Status reports `database_unavailable`.
  - When PostgreSQL returns, everything recovers without restarting the API.
  - The deployment smoke test proves this sequence.

## Structured logs

The Product writes one JSON completion record per observed Product operation. It uses the
existing schema (`event`, `operation`, `outcome`, `duration_ms`, `timestamp`,
`request_id`, `trace_id`/`span_id`, bounded details). The records go on the dedicated
`app.product.observability` logger to stdout.

- `APP_LOG_LEVEL` (DEBUG, INFO, WARNING, ERROR, CRITICAL) sets that logger's level.
  Completion records are INFO.
- The logger is configured once by the deployment factory, with no propagation, so
  there are no duplicates. The root logger is never reconfigured.
- Logs never contain:
  - request, conversation, Knowledge or ticket text, or approval notes;
  - query strings or headers, `Authorization`, credentials;
  - the database URL or the OTel endpoint;
  - exception text;
  - free-form company, store or actor identifiers.
- Logs are deployment output: there is no log API.

## OpenTelemetry export (optional)

`ProductObservability` stays the single observability abstraction, with exactly one
instance per application, handed to:

- the HTTP middleware;
- the Operations services, the Workflow engine;
- Knowledge, Approvals and Conversations.

The deployment factory builds it with `build_deployment_observability(settings)`.

| Setting | Values |
| --- | --- |
| `APP_OTEL_EXPORT_MODE` | `disabled` (default) or `otlp_http` |
| `APP_OTEL_EXPORT_ENDPOINT` | Required for `otlp_http`: an `http`/`https` collector **base** URL with a host. No credentials, query or fragment. A supplied value is validated by the same rules even while export is disabled; it is then unused. |

- **disabled:**
  - no SDK provider, exporter, processor, reader or thread is created;
  - no network connection is opened;
  - logs still work.
- **otlp_http:**
  - one SDK tracer provider (batch span processor) and one meter provider (periodic
    reader), passed explicitly to the Product observability;
  - no global provider is installed, so nothing else (Agno included) exports through
    them;
  - traces go only to `<endpoint>/v1/traces` and metrics only to
    `<endpoint>/v1/metrics`.
- **Resource:** only `service.name=agento`, `service.version` and
  `deployment.environment.name`. No host, company, store, key, backend or integration
  identifier is included, and no resource detector or `OTEL_RESOURCE_ATTRIBUTES` merge
  is used.
- **Data:** only the existing bounded Product observations, with the same metric names
  (`product.operation.count`, `product.operation.duration`). `request_id` may appear on
  spans, never as a metric attribute. No prompts, responses, conversation or Knowledge
  text, ticket descriptions, tool inputs or results, approval notes or exception
  strings are exported.
- **No exporter headers or credentials in v1.** If you need authenticated remote
  telemetry, run a private collector and let it authenticate onward. The template never
  passes `OTEL_*` SDK variables to the API.
- **No hidden exporter configuration.** The OpenTelemetry OTLP exporter would otherwise
  read its own `OTEL_EXPORTER_OTLP*` environment (headers, endpoints, certificates,
  compression, timeouts). With `otlp_http`, the API **refuses to start** if any variable
  named `OTEL_EXPORTER_OTLP` or `OTEL_EXPORTER_OTLP_*` is present. The refusal happens
  before any exporter is created, with a fixed error that names no variable or value:
  "OpenTelemetry exporter environment overrides are not allowed; configure Product
  telemetry with APP_OTEL_* settings only." With export disabled, no exporter exists,
  so such variables have no Product effect.
- **Best effort:**
  - an unreachable collector never fails a Product operation, readiness or System
    Status;
  - readiness never probes the collector;
  - System Status only reports the configured mode.
- **Shutdown:**
  - on application shutdown, every Product resource is released first;
  - telemetry is flushed and stopped last, exactly once;
  - each provider's shutdown is bounded (it runs in a daemon thread with a timeout);
  - a telemetry failure never blocks or fails the rest of shutdown.
- **Agno telemetry** stays disabled (`AGNO_TELEMETRY=false` or unset). Enabling it still
  fails closed. Product OTLP export is a separate, Product-owned concern.
- There is no `/metrics` endpoint, no Prometheus text exporter, no collector container,
  and no token or cost metric.

## Backup

`deployments/template/ops/backup.sh` is run by the operator. There is no scheduler and no
API or UI for it.

```bash
cd deployments/template
ops/backup.sh /secure/backups/agento-2026-10-03.dump
# optional: --project NAME --env-file FILE
```

- It is an **online**, **full** `pg_dump --format=custom` of the whole installation
  database. It runs inside the private PostgreSQL container over its local socket, so no
  password is passed or printed. The dump covers:
  - the Product schema and audit;
  - approvals, workflows, conversations and Knowledge;
  - Agent configuration and integration connection metadata;
  - the Agno runtime schema.
- File handling:
  - files are written under `umask 077` (mode 0600), to a temporary file in the target
    directory;
  - the dump is checked to be non-empty and custom-format before being renamed
    atomically;
  - a `.sha256` companion file is written;
  - an existing backup or checksum is never overwritten;
  - a failed `pg_dump` exits non-zero and leaves no file behind;
  - success means both final files were published and the checksum verifies;
  - a failed publication (for example the dump published but the checksum not) rolls
    back only the files this invocation published. Neither final artifact remains, and
    a pre-existing file or a concurrent backup's files are never removed (ownership is
    tracked explicitly). Of two concurrent backups to the same name, at most one
    succeeds;
  - nothing is uploaded or transferred.
- Schedule it, keep retention and copy it off the host with your own tooling.

### What the database backup does not contain

Integration credentials and backend secrets live **outside** PostgreSQL by design. The
database backup contains no integration secret values. A complete recovery set therefore
also needs your separately protected copies of:

- the deployment `.env`, with all runtime secrets, including the Product API key hashes
  and `OS_SECURITY_KEY`;
- `APP_INTEGRATION_SECRETS_DIR`, the integration secret store;
- `APP_BACKEND_SECRETS_DIR` and `APP_BACKEND_CONFIG_DIR`, the business backend inputs,
  once a real backend exists.

The backup tooling never copies those directories.

## Restore into an empty database

`deployments/template/ops/restore-into-empty.sh` is deliberately **not** a "replace the
live database" tool.

```bash
cd deployments/template
docker compose stop web api                 # nothing may serve the target database
docker compose down -v && docker compose up -d postgres   # or a new installation: an EMPTY database
ops/restore-into-empty.sh /secure/backups/agento-2026-10-03.dump
docker compose up -d                        # start the Product explicitly afterwards
```

It refuses before changing anything unless:

- the backup is a non-empty custom-format dump;
- the backup matches its `.sha256` file, when one is present (verified before any
  database access);
- the installation's PostgreSQL is reachable;
- `api`, `web` and `migrate` are not running;
- the target database has no Product, Agno runtime or other user schema or table.

It then:

1. restores the whole dump in one transaction (`--single-transaction --exit-on-error
   --no-owner --no-acl`);
2. runs the normal migration job (`alembic upgrade head`), so an older valid backup is
   upgraded;
3. verifies that the Product schema is at the image's head (`0008` in this build).

Alembic never touches Agno's schema. The script does not start the Product. Restore your
`.env` and secret/config directories before starting it.

## Recovery drill

`.github/scripts/backup-restore-drill.sh` runs in the Infrastructure CI job. It uses only
a disposable Compose project and volume (`cap-restore-drill`) and always removes them,
even on failure. The drill:

1. migrates and starts the installation;
2. disables the `operations` Agent through the Product API (the known state);
3. takes an online backup and checks the file:
   - custom format, mode 0600, checksum;
   - both `product` and `agno_runtime` are present;
   - no credential is in the dump;
4. proves that a backup never overwrites, and that a failed backup leaves nothing;
5. proves that restore refuses a live installation and a non-empty database, leaving it
   unchanged;
6. destroys the volume and starts an empty database;
7. proves that a tampered backup is refused before any change;
8. restores into the empty database and migrates (`0008`);
9. starts the Product, confirms it is ready, and confirms the `operations` Agent is
   still disabled.

## Current limitations

- **Network:** remote or public access still needs a separately reviewed TLS /
  reverse-proxy design. The API is never exposed directly, and only the Web is published,
  on `127.0.0.1`.
- **Backend:** there is no real business backend or provider yet, so staging and
  production refuse to start.
- **Release and runtime:** there is no release registry automation, orchestrator
  (Kubernetes, Helm, Terraform), auto-scaling or auto-healing worker.
- **Monitoring:** there is no alerting, pager, dashboard, log explorer or hosted
  monitoring. Point OTLP at your own collector.
