# Daily Operations Analysis Workflow

> **Status:** MVP deterministic workflow contract.

## Goal

Produce an evidence-backed report for the current Fulfly business day without modifying external systems.

## Inputs

- `company_id`
- `business_date`
- `company_timezone`
- FulFly integration reference
- Approved anomaly thresholds
- Caller and correlation context

## Preconditions

- Operations Agent is enabled in read/report mode.
- Caller has `operations.report.read`.
- FulFly credentials and currency are configured.
- FulFly account role has been verified as compatible with order listing.
- Company timezone is a valid IANA timezone.

## Workflow

### 1. Establish the reporting window

Convert the requested local business date into an inclusive start and exclusive end UTC window. Store both local and UTC boundaries in the run record.

### 2. Authenticate to FulFly

Request a currency-scoped JWT. Cache it only in protected short-lived storage and never log it. Refresh once after a `401`.

### 3. Fetch order pages

Call `GET /orders/affiliate-orders` using 1-based `page` and a configured `recordsPerPage`. Continue until the collected count reaches `totalOrders` or a contract-safe termination condition is met.

Because FulFly documents no date filter, filter `createdAt` locally. The adapter must track pages requested, records received, duplicates, invalid timestamps, and whether coverage is complete.

### 4. Normalise orders

Map provider responses to `CommerceOrder` snapshots. Preserve raw statuses and provider IDs. Reject malformed identifiers or timestamps into a quarantine/error collection rather than silently dropping them.

### 5. Enrich selected orders

Fetch order detail or status history only where it improves the analysis, for example:

- Non-terminal orders that may be stale.
- Orders with waiting-for-stock flags.
- Returned or cancelled orders needing event timing.
- Records missing fields required by a specific metric.

Bound concurrency and record per-order enrichment failures.

### 6. Read reference data

Fetch governorate shipping metadata when geographic cost or expected-duration analysis is enabled. Areas are fetched only when needed.

### 7. Read inventory conditionally

Run the inventory branch only when the capability check confirms an adequate response contract. Inventory failure must not invalidate the order report; it creates an explicit coverage warning.

### 8. Calculate metrics deterministically

Minimum metrics:

- Orders created in the reporting window.
- Count and percentage by raw status.
- Count waiting for stock.
- Count of terminal, cancelled, return-requested, and returned orders.
- Age distribution for non-terminal orders where timestamps are available.
- Geographic distribution by governorate.

Delivery rate is not published until the business definition of success and denominator is approved.

### 9. Detect anomaly candidates

Rules use configured thresholds, never model-generated thresholds. Each anomaly contains rule ID, severity, evidence IDs, affected count, observed value, threshold, and data-quality state.

### 10. Agent interpretation

Pass the deterministic result—not raw credentials or unrestricted payloads—to the Operations Agent. The agent explains and prioritises without changing numeric results.

### 11. Persist report and audit

Store the report, run status, coverage, redacted tool metadata, model usage, latency, and failure details. Return the report reference to the caller.

## Workflow states

```text
REQUESTED → RUNNING → COMPLETED
                    ↘ PARTIAL
                    ↘ FAILED
                    ↘ CANCELLED
```

`PARTIAL` is required when useful output exists but one or more sources or pages are incomplete.

## Idempotency

A logical run key should combine company, workflow version, business date, and requested mode. Manual reruns create a new attempt linked to the logical run; they do not overwrite previous reports or audits.

## Failure policy

| Failure | Behaviour |
|---|---|
| Invalid credentials | Fail; require configuration correction |
| Expired token | Re-authenticate once |
| Invalid currency | Fail as configuration error; do not retry repeatedly |
| Provider `422` | Fail relevant step; classify validation/permission |
| Transient network/`5xx` | Bounded exponential backoff |
| One detail/history failure | Continue as partial when aggregates remain valid |
| Incomplete order pagination | Mark partial and suppress whole-population rates |
| Inventory unavailable | Continue order report with warning |

## Evidence requirements

Every aggregate must be reproducible from stored normalised snapshots and workflow version. Reports refer to internal evidence IDs, not raw PII.

## Acceptance criteria

- The business-day boundary is tested across timezone and daylight-saving cases.
- Pagination does not skip or duplicate records silently.
- A report never claims complete coverage after a page failure.
- Core metrics are deterministic and stable across repeated runs on the same fixtures.
- The agent cannot alter calculated values.
- No write endpoint is called.
- All tool calls and policy decisions are audited.
- Inventory unavailability is degraded gracefully.

