# Production Deployment

> **Status:** Deployment requirements baseline; current repository manifests have not yet been verified against every requirement in this supplemental document.

## Isolation

Each company receives a dedicated deployment with separate:

- Application and worker processes.
- PostgreSQL database.
- Redis instance or physically isolated service.
- Object-storage namespace and credentials.
- Secrets and integration credentials.
- Knowledge and configuration.
- Backups and telemetry access.

There is no shared multi-company database or central tenant control plane.

## Release requirements

- Immutable versioned application image.
- Pinned Agno and dependency versions.
- Reproducible build with dependency lock files.
- Database migration plan and tested backup.
- Health, readiness, and dependency checks.
- Resource limits and restart policy.
- TLS for public ingress.
- Secret injection at runtime.
- Centralised redacted logs and metrics.
- Rollback procedure tested against the release.

## Deployment sequence

1. Validate configuration and secret references.
2. Back up data and verify backup completion.
3. Apply compatible database migrations.
4. Deploy backend and workers with write capabilities disabled for the MVP.
5. Run internal health and integration contract checks.
6. Deploy frontend.
7. Run smoke tests using non-sensitive records.
8. Enable schedules only after manual verification.
9. Observe error rate, latency, provider failures, and report coverage.

## FulFly controls

- Restrict outbound calls to the documented HTTPS base URL.
- Store the login JWT only in protected short-lived storage.
- Configure low concurrency until rate limits are confirmed.
- Use bounded retry for transient failures.
- Treat `500 Invalid currency` as configuration failure, not transient server failure.
- Reconcile webhook notifications through authenticated reads.
- Never enable documented write endpoints in the Integration 001 tool allowlist.

## Backup and recovery

Define recovery-point and recovery-time objectives before production. Backups must cover PostgreSQL, report artifacts, configuration versions, and required audit data. Redis should not be the sole durable store for workflow or approval state.

Restore must be tested, not inferred from backup success.

## Observability gate

Dashboards and alerts should cover:

- API and workflow success/failure.
- FulFly latency and error classification.
- Pagination completeness and partial reports.
- Token refresh failures.
- Queue depth and worker health.
- Model latency, token usage, and cost.
- Audit-write failures.
- Unusual webhook volume.

## Rollback

Rollback must account for database compatibility. If a migration is not backward-compatible, release promotion requires a tested forward-fix or restore plan. Never roll back application code blindly across an incompatible schema.
