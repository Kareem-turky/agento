# Production deployment and Integration 001

> **Status:** "Current deployment" is `VERIFIED_CURRENT_PRODUCT` (from
> [`../../PRODUCTION_OPERATIONS.md`](../../PRODUCTION_OPERATIONS.md) and
> [`../../MVP_RELEASE_ACCEPTANCE.md`](../../MVP_RELEASE_ACCEPTANCE.md)). "Integration 001
> additions" is `PROPOSED_FUTURE`.

## Current deployment (authoritative: the canonical docs)

What already exists:

- Docker images for the API and Web, and the packaged `deployments/template` Compose
  installation (PostgreSQL, migrations, API with AgentOS, Web/BFF), non-root and with a
  read-only root filesystem.
- `GET /health/live` and `GET /health/ready` (public). Readiness requires the application
  lifespan, the attached Agent runtime, a reachable PostgreSQL and the expected schema
  revision; it never depends on the Operations Agent, a model, an integration, a
  connection test, Redis or telemetry.
- `GET /api/v1/system/status` (`system.read`).
- Operator online backup and restore-into-empty tooling, proven by the CI backup/restore
  drill.
- Deployment smoke and demo smoke in the Infrastructure CI job, and the Product release
  and regression gate.

Current limitations that Integration 001 does **not** remove:

- There is no reviewed public reverse proxy / TLS ingress design. Only the Web is
  published, on `127.0.0.1`; the API is never exposed directly.
- There is no real business backend, so staging and production refuse to start.

## Integration 001 additions (`PROPOSED_FUTURE`, after the gate)

- Outbound access to the FulFly HTTPS origin only, through `app/integrations/http/`.
- FulFly credentials only in the chosen secret mechanism (gate 5); never in images,
  Compose files, Git or logs.
- Low request concurrency until FulFly documents rate limits; bounded retries for
  transient failures; `500 Invalid currency` treated as a configuration error, not
  retried.
- No inbound endpoint for FulFly (no webhook route; polling/authenticated reads only).
- Readiness stays independent of FulFly.
- A FulFly-backed staging/production start requires a reviewed backend registration and
  its own release acceptance; none exists.
