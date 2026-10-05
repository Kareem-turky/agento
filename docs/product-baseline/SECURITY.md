# Security

> **Status:** Security requirements baseline. This is not a claim that an implementation has passed security review.

## Security objectives

- Preserve physical isolation between company deployments.
- Prevent unauthorised tool use and privilege expansion.
- Prevent LLM content from bypassing deterministic policy.
- Protect integration credentials and customer PII.
- Make externally visible actions attributable and auditable.
- Detect provider-contract violations and incomplete data.
- Recover safely without duplicating high-impact actions.

## Trust boundaries

Untrusted by default:

- User prompts.
- Customer names, messages, addresses, and notes.
- FulFly and other provider responses.
- Webhook payloads.
- Retrieved documents and websites.
- Model output.
- MCP/server tool descriptions and output.

Trusted only after explicit validation:

- Authenticated identity claims.
- Permission and policy decisions from platform code.
- Validated configuration.
- Deterministic workflow calculations.
- Tool results that pass schema and post-execution verification.

## Threats and controls

### Prompt injection

Controls:

- Treat provider/customer text as quoted data, never instructions.
- Expose only capability-filtered tools.
- Keep policy enforcement outside model prompts.
- Never place credentials in model context.
- Test adversarial strings in product titles, customer names, notes, and knowledge documents.

### Tool abuse

Controls:

- Narrow tools with strict schemas.
- Default-deny permission and agent manifests.
- Risk classification and approval for writes.
- Resource ownership checks inside the tool, not only in the UI.
- Timeouts, bounded retries, and concurrency limits.

### Credential exposure

Controls:

- Secret manager references in configuration.
- Redaction in logs, traces, errors, and audits.
- Short-lived token storage.
- Separate development and production credentials.
- Rotation and incident procedure.

### Webhook forgery and replay

FulFly's documented webhook has no signature or event ID. Until stronger authentication is supplied:

- Accept webhook input as an untrusted hint.
- Validate schema and size.
- Apply endpoint rate limits.
- Do not mutate order state solely from the webhook.
- Re-read the order/history through authenticated API calls.
- Deduplicate best-effort using order, status, and timestamp.
- Store minimal raw metadata with retention controls.

### PII leakage

Controls:

- Restrict PII fields to operational access roles.
- Mask phone numbers in logs and standard reports.
- Avoid sending PII to models unless the use case explicitly requires it.
- Set retention and deletion policies before production ingestion.
- Encrypt data in transit and at rest.

### Incomplete or misleading analytics

Controls:

- Track page coverage and source freshness.
- Mark partial runs explicitly.
- Suppress whole-population KPIs when coverage is incomplete.
- Preserve metric and workflow versions.
- Require evidence references for anomalies.

## Secrets

Never store:

- Provider passwords or JWTs in Git.
- Secrets in YAML committed to source control.
- Secrets in agent memory or knowledge bases.
- Full authorisation headers in logs or traces.
- Production payloads in test fixtures.

Production secret access should be scoped to the service that needs it and audited.

## Network controls

- HTTPS for all provider and public API traffic.
- Database and Redis not exposed publicly.
- Outbound allowlisting where practical.
- Separate ingress for API and webhook paths.
- Request-body limits and timeouts.
- Administrative endpoints restricted to trusted networks or strong authentication.

## Audit integrity

Audit records should be append-only for application identities. Corrections are new linked records, not silent edits. High-value records should be protected by database permissions, retention controls, and optionally tamper-evident hashing.

## Incident minimums

An incident procedure must cover:

- Credential exposure and rotation.
- Provider compromise or unexpected payload behaviour.
- Cross-company data exposure.
- Unauthorised external action.
- Prompt-injection success.
- Audit/logging outage.
- Corrupted or misleading reports.

## Production security gate

Before live customer data:

- Threat model reviewed.
- Secrets and redaction verified.
- Permission and policy tests pass.
- Prompt-injection suite passes.
- Dependency and container scans pass at the agreed severity threshold.
- Backups and restore tested.
- Logs contain no tokens or unmasked phone numbers.
- FulFly webhook limitation is accepted and mitigated.
- No write tool is available in the MVP runtime.

