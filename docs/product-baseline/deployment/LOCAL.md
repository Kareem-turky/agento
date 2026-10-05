# Local development and Integration 001

> **Status:** `VERIFIED_CURRENT_PRODUCT` for existing facts (from the repository
> [`README.md`](../../../README.md)); Integration 001 rules are
> `APPROVED_INTEGRATION_DECISION`.

## Current local setup (authoritative: the repository README)

- `./scripts/demo.sh up` / `down` / `reset` / `credentials` / `status` runs the Product
  demo against the deterministic `mock` business backend.
- The root `docker-compose.yml` provides local PostgreSQL (pgvector) and Redis bound to
  `127.0.0.1`. Redis is not a readiness dependency.
- The OpenAPI artifact is regenerated and checked with
  `uv run python apps/api/scripts/export_product_openapi.py` (`--check`).

Use the README for exact commands; this file does not repeat them.

## Integration 001 rules for local work

- CI and default local runs make **no** live FulFly calls. Adapter work uses synthetic
  fixtures with no production PII or credentials.
- Live FulFly checks are opt-in, manual, and use a dedicated non-production FulFly
  account when one exists.
- Never place FulFly credentials or JWTs in the repository, fixtures, screenshots,
  prompts or logs.
- No FulFly write endpoint is ever called, locally or otherwise.
