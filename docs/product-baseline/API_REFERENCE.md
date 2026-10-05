# Agento API Reference

> **Status:** Proposed product boundary for Integration 001. This is supplemental design guidance and does not replace the repository's existing generated or implementation-level API reference.

## API principles

- Version every public route.
- Authenticate before resolving company resources.
- Return stable domain contracts, never Agno or provider response objects.
- Use correlation IDs across API, workflow, tools, and audit.
- Represent partial data explicitly.
- Never return secrets or raw provider authorisation data.

## Proposed MVP endpoints

### `POST /api/v1/operations/reports`

Starts or reuses a daily operations analysis run.

Request:

```json
{
  "business_date": "2026-10-05",
  "timezone": "Africa/Cairo"
}
```

Response `202`:

```json
{
  "run_id": "...",
  "status": "REQUESTED",
  "correlation_id": "..."
}
```

Permission: `operations.report.run`.

### `GET /api/v1/operations/runs/{run_id}`

Returns workflow state, coverage, progress metadata, and error classification without exposing secrets or customer PII.

### `GET /api/v1/operations/reports/{report_id}`

Returns the completed or partial report and evidence summaries allowed for the caller.

Permission: `operations.report.read`.

### `GET /api/v1/integrations/fulfly/health`

Administrative health check for DNS/TLS reachability, authentication, currency access, provider role, and contract version. It must not return tokens, credentials, or customer records.

Permission: `integrations.health.read`.

### `POST /api/v1/webhooks/fulfly/order-status`

Receives untrusted FulFly status notifications. Schema-valid events enqueue authenticated reconciliation. Because the documented provider webhook has no signature, receipt must never directly mutate authoritative order state.

## Standard response metadata

```json
{
  "data": {},
  "meta": {
    "correlation_id": "...",
    "generated_at": "...",
    "coverage": "complete|partial|unknown",
    "warnings": []
  }
}
```

## Standard error

```json
{
  "error": {
    "code": "PERMISSION_DENIED",
    "message": "The requested operation is not allowed.",
    "correlation_id": "...",
    "details": []
  }
}
```

Do not forward provider error text directly to external clients if it may reveal identifiers or configuration.

## Recommended status codes

| Status | Meaning |
|---|---|
| `200` | Successful read |
| `202` | Workflow accepted |
| `400` | Invalid request contract |
| `401` | Caller not authenticated |
| `403` | Capability denied |
| `404` | Resource absent or outside visible scope |
| `409` | State/idempotency conflict |
| `422` | Valid JSON but business validation failed |
| `429` | Agento rate limit |
| `502` | Provider contract or upstream response failure |
| `503` | Required dependency unavailable |

## OpenAPI requirement

When the backend exists, FastAPI's generated OpenAPI document must be reviewed, versioned, and used for contract tests. This document must then link to the exact schema and remove proposed routes that were not implemented.
