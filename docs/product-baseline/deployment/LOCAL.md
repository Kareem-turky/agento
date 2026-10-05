# Local Development

> **Status:** Supplemental environment expectations. Exact executable commands must be verified against the repository's current manifests.

## Intended services

- Backend API and worker.
- Next.js frontend.
- PostgreSQL with pgvector.
- Redis.
- S3-compatible local object storage when report artifacts require it.
- OpenTelemetry collector or a documented local no-op configuration.

## Local safety rules

- Use demo fixtures by default.
- Never connect production credentials from a developer checkout unless an approved diagnostic procedure requires it.
- Keep local secret files ignored by Git.
- Bind databases and Redis to local interfaces only.
- Use clearly labelled development buckets and databases.
- Disable external write tools.

## Expected developer checks

A clean local setup must prove:

1. Services start from documented manifests.
2. Migrations complete.
3. Demo data loads.
4. Backend health succeeds.
5. Frontend can authenticate against the local backend.
6. Daily operations analysis completes on demo data.
7. Report, audit, and metrics records exist.
8. All automated test suites pass.

## Required future documentation

Reconcile this file with exact prerequisites, setup commands, ports, seed commands, reset procedures, and expected output from a clean machine. Do not document destructive database reset commands without explicit scope and recovery warnings.
