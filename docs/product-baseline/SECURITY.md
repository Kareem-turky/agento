# Security requirements for Integration 001

> **Status:** "Existing controls" is `VERIFIED_CURRENT_PRODUCT`. FulFly-specific
> requirements are `APPROVED_INTEGRATION_DECISION` (principles) and apply to any future
> adapter. This is not a claim that any FulFly code exists or passed a security review.

## Existing controls this integration relies on

- Product API key authentication; governance (`GovernanceGate` → policy →
  `ExecutionCoordinator` → audit) on every business action.
- Credential values only in `IntegrationSecretStore` (never PostgreSQL, responses, logs,
  audit or error details); config fields that look like credentials are refused.
- Secure outbound transport `app/integrations/http/` for reviewed adapters only; Agents,
  Workflows and models never receive a transport or credentials.
- Agent and tool output are untrusted; the Operations Agent has a fixed tool set.
- Canonical models reject unknown fields; adapters raise `IntegrationDataError` instead
  of guessing.

## FulFly-specific requirements

### Credentials and JWT

- `email` and `password` live only in the chosen secret mechanism (gate 5).
- The JWT is derived from the credentials, cached only short-term in process, never
  stored as metadata or in a database, never returned, never logged, never audited, and
  refreshed by re-authentication after an appropriate authentication failure.
- The `Authorization` header and the `currency` header value are redacted from any
  diagnostic output.

### Untrusted provider data

- Every FulFly field (customer names, notes, product titles, error messages) is
  untrusted data. Provider text never becomes instructions, tool choices or policy.
- Provider error text is never forwarded to Product clients or the model.
- Customer PII (names, phones, addresses) is not mapped into canonical models and must
  not be logged.

### Honest data

- No fabricated entities: no `Shipment` from order statuses, no inferred SLAs from
  `deliveredIn`, no zero or default values for missing money, items or timestamps.
- No completeness claim from page counts (see
  [workflows/DAILY_OPERATIONS_ANALYSIS.md](workflows/DAILY_OPERATIONS_ANALYSIS.md#pagination-coverage-blocker)).

### No provider writes

FulFly Integration 001 introduces no external/provider write capability. The adapter
must not call any FulFly write endpoint (order create/cancel, product create, image
upload, XLSX export). Existing Agento internal/governed ticket and approval capabilities
remain unchanged.

## Webhook discovery notes

> **Status:** `VERIFIED_PROVIDER_DOC` for FulFly behaviour; ingestion is
> `PROPOSED_FUTURE` and out of scope.

The initial scope is polling / authenticated reads only. Webhook ingestion would be a
separate reviewed task (the Product has no webhook or ingest route today).

What FulFly documents: one POST about three seconds after a status change, five-second
timeout, no retry, no notification on creation, `updatedAt` is send time, and **no
signature, shared secret, event id or replay protection**.

Implications for any future ingestion design:

- A webhook body is only an untrusted hint; it must never change state directly.
- Any state must come from an authenticated re-read of the order.
- A missed webhook is normal (no retry), so polling remains the source of truth.
- A public inbound endpoint would also require the reviewed TLS/ingress design that the
  Product does not have yet.

## Release minimums before live FulFly data (`PROPOSED_FUTURE`)

- The implementation gate is closed ([INTEGRATION_001_DECISIONS.md](INTEGRATION_001_DECISIONS.md#implementation-gate)).
- Adapter passes the commerce conformance harness and FulFly contract tests on
  synthetic fixtures.
- Log and audit scans show no JWT, password, `Authorization` header or unmasked phone.
- Prompt-injection strings in provider text fields do not change Agent behaviour.
- No FulFly write endpoint is reachable from the adapter.
