# Test Strategy

> **Status:** Required strategy and MVP acceptance suite.

## Test layers

### Unit tests

- Status mapping.
- Money and timezone handling.
- Provider error normalisation.
- Input validation and redaction.
- Anomaly rules and severity.
- Report rendering from deterministic results.

### Adapter contract tests

- Request method, URL, headers, and body.
- Authentication and 12-hour token refresh behaviour.
- Currency header on every FulFly request.
- Pagination and `totalOrders` accounting.
- Optional/role-scoped response fields.
- Multiple error-envelope shapes.
- Provider contract violations and unknown fields.

Use recorded synthetic fixtures that contain no production PII or credentials.

### Integration tests

- PostgreSQL persistence and migrations.
- Redis queue/lock behaviour.
- Report artifact storage.
- Audit and telemetry emission.
- Workflow retries and partial-state persistence.

### Permission and policy tests

- Allowed user + allowed agent.
- User denied.
- Agent manifest denied.
- Unknown capability denied.
- Cross-scope provider ID rejected.
- Policy service failure fails closed.
- No write tool exposed in MVP.

### Workflow tests

- Empty day.
- Single and multiple pages.
- Duplicate orders between pages.
- Invalid timestamp.
- Detail/history partial failure.
- Inventory unavailable.
- Token expires during run.
- Invalid currency.
- Provider timeout and recovery.
- Stable rerun results.

### Agent tests

- Numeric values remain identical to deterministic input.
- Facts and interpretations are separated.
- Evidence is cited for anomalies.
- PII is omitted.
- Missing sources are disclosed.
- No unsupported root cause is asserted.
- Prompt injection in every text-bearing provider field is ignored.

### End-to-end tests

From authenticated report request to stored report, audit trail, and metrics. Run first with demo data and then against an approved FulFly sandbox/test account if available.

## FulFly fixture matrix

Include:

- All documented statuses.
- Role-scoped missing fields.
- Multiple currencies and invalid currency.
- `401`, `404`, `422`, and documented `500` envelopes.
- Bare string and `{msg}` errors.
- Empty variants response.
- Oldest-first status history.
- Webhook duplicate, delayed, invalid, and unverifiable events.

## Security tests

- Secret redaction in logs, errors, traces, audits, and model context.
- Oversized webhook and API bodies.
- Malformed IDs and header injection.
- Prompt injection in customer name, notes, product title, and retrieved knowledge.
- SSRF protections for configurable URLs.
- Authentication and authorisation bypass attempts.
- Dependency and container vulnerability scanning.

## MVP release acceptance

- All unit and integration tests pass.
- FulFly adapter contract suite passes.
- Operations workflow produces correct fixture KPIs.
- Incomplete pagination produces `PARTIAL`, not `COMPLETED`.
- Inventory failure does not break the order report.
- No write endpoint is called in any MVP test.
- Reports contain timezone, freshness, coverage, evidence, and limitations.
- Logs contain no JWT, password, full authorisation header, or unmasked phone number.
- Audit events exist for run, policy decisions, tool calls, and final result.
- Critical security tests pass with no unresolved critical finding.

## Test evidence

CI should preserve test summaries, coverage reports, contract-fixture version, migration checks, security-scan results, and the application image digest used for release.

