# Troubleshooting Runbook

> **Status:** Safe diagnostic baseline. Commands and dashboards must be linked after implementation.

## Diagnostic order

1. Identify company, environment, run ID, and correlation ID.
2. Confirm whether the run is failed, partial, or merely delayed.
3. Check dependency health without exposing secrets.
4. Inspect normalised error category and provider status.
5. Confirm data coverage before trusting the report.
6. Apply the narrowest recovery action.
7. Verify recovery and record the incident/audit note.

## FulFly authentication failure

Symptoms: login `404`, repeated `401`, or no token.

Checks:

- Secret references resolve without printing values.
- Account is active.
- Currency header is present during login.
- System clock is accurate for JWT handling.
- Token cache is not serving an expired token.

Recovery: correct/rotate credentials through secret management, perform a health check, and rerun the failed workflow attempt. Never paste credentials into tickets or logs.

## Invalid currency

Symptom: HTTP `500` with `Invalid currency`.

Meaning: configuration/access problem, not a transient provider outage.

Checks:

- Currency ID is configured and sent on every request.
- The authenticated account can use that currency.
- Currency access was present when the JWT was issued.

Recovery: correct the currency or sign in again after access changes. Do not use repeated automatic retry.

## Insufficient permissions

Symptom: `422 Insufficient permissions`.

Checks:

- FulFly account role.
- Endpoint role requirements.
- Individual capability flags managed by FulFly.
- Agento capability and manifest decision.

Impact: an Affiliate credential is required for the documented order-list endpoint. A Seller credential alone cannot support the documented daily order-ingestion flow.

## Incomplete pagination

Symptoms: received count does not match `totalOrders`, repeated pages, or page failure.

Response:

- Mark run `PARTIAL`.
- Suppress rates that require complete population coverage.
- Preserve successfully processed pages.
- Retry only the failed page within the configured budget.
- Investigate provider page-size behaviour and data changes during pagination.

## Report has no orders

Checks:

- Requested business date and `Africa/Cairo` boundary.
- Timestamp parsing and UTC conversion.
- FulFly account scope and currency.
- Pages were fetched completely.
- Local date filtering did not discard invalid timestamps silently.

An empty result is valid only when coverage is complete and all timestamps were processed.

## Inventory unavailable

Likely causes:

- `/all-product-variants` schema not verified.
- Account role incompatible with seller endpoint.
- Product IDs unavailable.
- Provider response omitted stock fields.

Response: continue the order report with `inventory_available=false`. Do not describe missing inventory as zero stock.

## Webhook not received

FulFly makes one attempt, waits five seconds, and does not retry. Check public HTTPS reachability, receiver latency, schema rejection, and rate limiting. The scheduled/polling reconciliation remains the recovery mechanism.

## Unexpected webhook

Do not trust it. Validate schema, rate-limit the sender, enqueue an authenticated order/history read, and update state only from reconciled provider data. Investigate unusual volume as a possible abuse signal.

## Provider contract changed

Symptoms: schema validation failures, missing required fields, new error shape, or type changes.

Response:

1. Stop promoting affected runs as complete.
2. Preserve redacted failing examples.
3. Mark the adapter contract version unhealthy.
4. Update fixtures and mapping only after review.
5. Run contract, workflow, and report regression tests.
6. Deploy with a documented migration/compatibility decision.

## Audit logging failure

External tool execution must fail closed if its required audit record cannot be written. Read-only reporting may continue only if policy explicitly permits buffered durable audit delivery; silent loss is forbidden.

## Escalation information

Provide:

- Environment and deployment version.
- Run and correlation IDs.
- Business date/timezone.
- Normalised error category and provider HTTP status.
- Affected capability and page/order IDs where permitted.
- Coverage state and retry count.
- Redacted timestamps and messages.

Never provide passwords, JWTs, authorisation headers, or full customer payloads.

