# Agento Product API Reference

This is the developer reference for the **Agento Product API**, version 1. The
machine-readable contract is [`openapi/agento-product-api-v1.json`](openapi/agento-product-api-v1.json)
(OpenAPI 3.1). Both are generated from the Product source by
`apps/api/scripts/export_product_openapi.py`, and CI fails if they drift from the code.
Sections 11 and Appendix A are generated; everything else is maintained by hand.

Contents:

1. [Scope](#1-scope)
2. [Base URL](#2-base-url)
3. [Versioning](#3-versioning)
4. [Authentication](#4-authentication)
5. [Authorization and scope](#5-authorization-and-scope)
6. [Request and response conventions](#6-request-and-response-conventions)
7. [Idempotency](#7-idempotency)
8. [Errors](#8-errors)
9. [Important boundaries](#9-important-boundaries)
10. [Examples](#10-examples)
11. [Endpoint reference (generated)](#11-endpoint-reference-generated)
- [Appendix A. Schemas (generated)](#appendix-a-schemas-generated)

## 1. Scope

This reference describes the **Agento Product API only**: 59 operations (method + path),
grouped as Health, System, Operations, Integrations, Agents, Skills, Tasks, Workflows,
Knowledge, Approvals, Conversations and Employee Chat.

Three different HTTP surfaces exist in a running installation, and only the first one is
this contract:

| Surface | Paths | Who uses it |
| --- | --- | --- |
| **Agento Product API** (this reference) | `/api/v1/...` and the public `/health`, `/health/live`, `/health/ready` | Integrations, scripts, tools, and the Agento Web app's server side. |
| AgentOS runtime API (internal) | `/agents`, `/teams`, `/sessions`, `/info`, `/config`, `/memories`, `/traces` and other Agno runtime routes | The internal Agno runtime, protected by `OS_SECURITY_KEY`. **Not part of the Product contract**; it may change with the pinned Agno version and is not documented here. |
| Agento Web BFF | `/api/product/...` on the Web service | Only the Agento browser UI, through its own server. An implementation detail of the Web app, **not** an API contract. |

Subsystem behaviour is explained in more depth in [`INTEGRATIONS.md`](INTEGRATIONS.md),
[`AGENTS.md`](AGENTS.md), [`SKILLS_AND_TASKS.md`](SKILLS_AND_TASKS.md),
[`WORKFLOWS.md`](WORKFLOWS.md), [`KNOWLEDGE.md`](KNOWLEDGE.md),
[`APPROVALS.md`](APPROVALS.md), [`CONVERSATIONS.md`](CONVERSATIONS.md),
[`EMPLOYEE_CHAT.md`](EMPLOYEE_CHAT.md) and
[`PRODUCTION_OPERATIONS.md`](PRODUCTION_OPERATIONS.md).

## 2. Base URL

The base URL is **deployment-specific**, so the OpenAPI document lists no server. In the
examples it is `$AGENTO_BASE_URL`.

- **Deployment package** (`deployments/template`): the Product API service listens on
  port 8000 **inside the private Compose network only**. It is not published to the host.
  The only host-facing service is the Web app, bound to `127.0.0.1`. Do not publish the
  API port: remote access needs a separately reviewed TLS / reverse-proxy design, which
  this project does not yet provide.
- **Source-tree local development** (see the README, "8. Run the API"): the API runs
  directly with `uv run uvicorn app.bootstrap:create_deployment_app --factory --app-dir
  apps/api --env-file .env --port 8000`. In that setup the base URL is
  `http://localhost:8000`.
- `/docs`, `/redoc` and `/openapi.json` exist only in local/test environments, as
  development conveniences. They describe the **combined** process, Product plus AgentOS,
  and they are not this contract. Use the checked-in OpenAPI file instead.

## 3. Versioning

- Product endpoints are versioned in the path: `/api/v1/...`.
- The health endpoints `/health`, `/health/live` and `/health/ready` are intentionally
  unversioned.
- The artifact's `info.version` is the Product application version. No compatibility or
  deprecation policy has been defined yet beyond the `/api/v1` path prefix. Treat the
  checked-in OpenAPI file of the release you deploy as the contract of that release.

## 4. Authentication

Every `/api/v1/...` endpoint requires a **Product API key**:

```
Authorization: Bearer <Product API key>
```

- **Header format:** send exactly one `Authorization` header, with scheme `Bearer`.
- **Key format:** the key is 32-256 printable ASCII characters with no whitespace.
- **Server storage:** the server stores only SHA-256 hashes of configured keys
  (`APP_PRODUCT_API_KEYS`).
- **Failure:** a missing, malformed or unknown key is **401** `{"detail": "Not
  authenticated"}`. The reason is never revealed.
- **Public health:** `/health`, `/health/live` and `/health/ready` need no credential.
- **`OS_SECURITY_KEY` is not a Product credential.** It protects the internal AgentOS
  runtime routes only and is never accepted on Product routes. The installation refuses
  to start if a configured Product key equals it.
- Identity headers such as `X-Actor-Id`, `X-Company-Id`, `X-Permissions` or
  `X-Store-Ids` are ignored. Identity comes only from the key.

In OpenAPI this is the `ProductApiKey` security scheme (HTTP Bearer). Every `/api/v1`
operation references it; the health operations do not.

## 5. Authorization and scope

- **Company.** One installation serves one company. The company comes from the
  authenticated principal; a client never submits it. Where a body could carry a
  `company_id`, the strict models refuse it.
- **Principal.** Each key maps to one principal: an actor id, permissions and granted
  store ids.
- **Stores.** A `store_id` in a body or query is a **selector**, checked against the
  principal's *current* store grants:
  - an ungranted store is **403**;
  - threads, proposals and ticket commands in a store that is no longer granted read as
    **404**.
- **Permissions.** Each endpoint states its Product permission in section 11, for example
  `integrations.read`, `knowledge.manage`, `approvals.decide` or `tickets.create`. A
  missing permission is **403**.
- **Business actions.** For governed business actions the permission is enforced by the
  governance gate on the action itself. Examples: the Operations Agent's read tools need
  `orders.read`, `shipments.read` and `stores.read`, and a ticket needs `tickets.create`.
- **Foreign resources.** A resource of another company or another actor, where
  ownership applies, is indistinguishable from a missing one (**404**).

## 6. Request and response conventions

- **JSON in, JSON out.** Request bodies are `application/json`. Responses are JSON,
  including errors.
- **Strict validation.** Request models reject unknown fields (`extra="forbid"`; marked
  *Strict* in Appendix A). Strings are typed and bounded, and ids are UUIDs unless the
  schema says otherwise (for example `agent_id`, `workflow_id`).
- **Fixed paths.** Resource ids are passed as **query parameters** or body fields, never
  as path segments. For example: `GET /api/v1/chat/thread?thread_id=...`.
- **Safe validation errors.** A **422** lists only `type`, `loc` and `msg` for each error
  (`SafeValidationError`). Submitted values are never echoed: no message, title,
  credential or malformed value.
- **Request id.** Every response carries a server-generated `X-Request-ID` (UUID), and
  many bodies repeat it as `request_id`. Quote it to correlate logs and support. Any
  client-sent `X-Request-ID` is ignored. It is never identity, scope or authority.
- **Timestamps** are ISO 8601 with a time zone.
- **Untrusted text.** Knowledge text, conversation messages and Employee Chat answers are
  untrusted text. Display them as plain text, never as HTML or instructions.

## 7. Idempotency

Exactly **two** operations take an `Idempotency-Key` header. Both require it exactly once;
a missing or repeated key is **400**.

| Operation | Purpose |
| --- | --- |
| `POST /api/v1/operations/tickets` | Create one operational ticket (the governed `operations.ticket.create` write). |
| `POST /api/v1/chat/ticket-proposals/confirm` | Explicitly confirm one stored Employee Chat ticket proposal (the same governed write). |

The key:

- is **opaque**: 1-128 characters from `A-Z a-z 0-9 . _ : ~ -`;
- is generated by the client;
- is **never** a business value, and is never returned, logged or stored in plaintext.

Retry rules:

- Reuse the key **only** to retry the *same* intent or proposal.
- A retry **replays** the durable command (`replayed: true`). It never executes twice.
- The same key with a different request is **409** (idempotency conflict).
- A confirmed proposal is bound to its first key. Another key is **409**.

No other operation reads this header. Employee Chat turns are made idempotent by the
client-generated `turn_id` field instead.

## 8. Errors

Error bodies are JSON `{"detail": "<fixed message>"}`, except 422 (section 6). Messages
are fixed text and never contain submitted values. Not every status applies to every
endpoint; section 11 lists each endpoint's statuses.

| Status | Meaning |
| --- | --- |
| 400 | `Idempotency-Key` missing, repeated or invalid (the two idempotent operations only). |
| 401 | Missing or invalid Product API key (`Not authenticated`). |
| 403 | Authenticated but not permitted: missing Product permission, or a store selector not granted. |
| 404 | Unknown resource. Also used for another company's or actor's resource, or one in a store no longer granted, so existence is never revealed. |
| 409 | State conflict. Examples: Agent disabled, idempotency conflict, approval already decided, proposal already confirmed or cancelled, Employee Chat turn conflict. |
| 413 | Not returned by the Product API. The Agento Web BFF has its own request-size caps. |
| 422 | Request validation failed (`SafeValidationError`, no values echoed); for example an unsupported query parameter on the daily report. |
| 503 | A required Product service is not available in this deployment, or failed. Fixed message, no internals. |

Successful writes can also answer **202** (accepted, outcome not yet confirmed). On the
ticket operations the HTTP status reports how the command was *processed*. The body's
`status` is the business outcome: **only `verified` means the ticket exists**.

## 9. Important boundaries

These are deliberate negative capabilities of the current Product API.

- **Operations analysis is read-only.** `POST /api/v1/operations/runs` runs the
  Operations Agent with **no** write capability.
- **No generic execution.** There is no `/execute` endpoint and no endpoint that takes
  an arbitrary action name. The only business write is the fixed operational ticket
  (`operations.ticket.create`), through two governed paths:
  - `POST /api/v1/operations/tickets`;
  - an Employee Chat proposal confirmation.
- **Approvals cannot be created by clients.** Approval requests exist only because
  governance required one for a real action. There is no public create endpoint.
- **No Workflow run endpoint.** Workflows are read-only to the API; they run only inside
  trusted Product services (for example the daily report).
- **Conversations are read-only.** There is no ingest, webhook, send or reply endpoint,
  and no public Customer/CX messaging send API exists.
- **Employee Chat is internal.** It is a chat between an authenticated **employee** and
  the Operations Agent. It is not Customer Chat and has no customer identity or channel.
- **Employee Chat can only propose.** The Agent may only **propose**
  `operations.ticket.create`. A proposal executes nothing.
- **Confirmation uses the stored proposal.** A confirmation executes the **stored**
  proposal; the client sends only `proposal_id` and the `Idempotency-Key`. Title,
  description, action and store are never resubmitted or accepted.
- **Integrations manage connections only.** Integration management covers connection
  **lifecycle** (create, update, credentials, test, enable, disable, delete). It does not
  import or sync business data. This build installs no provider integration.
- **Agents are configured, not authored.** Agent management can enable, disable or reset
  installed Product Agents. It cannot create Agents, edit instructions or choose models
  or tools.

## 10. Examples

Placeholders: `$AGENTO_BASE_URL` (section 2), `$AGENTO_API_KEY` (a Product API key, never
`OS_SECURITY_KEY`), `$STORE_ID` (a granted store UUID). Generate UUIDs and idempotency
keys on the client (for example `uuidgen`).

```bash
# Health (public)
curl -s "$AGENTO_BASE_URL/health/live"            # {"status":"alive"}
curl -s "$AGENTO_BASE_URL/health/ready"           # {"status":"ready"} or 503 {"status":"not_ready"}

AUTH="Authorization: Bearer $AGENTO_API_KEY"

# Operations analysis (read-only Operations Agent run)
curl -s -X POST "$AGENTO_BASE_URL/api/v1/operations/runs" -H "$AUTH" \
  -H "Content-Type: application/json" \
  -d "{\"store_id\": \"$STORE_ID\", \"message\": \"Analyze operations for 2026-03-03.\"}"

# Daily Operations Report (deterministic; business_date optional)
curl -s "$AGENTO_BASE_URL/api/v1/operations/reports/daily?store_id=$STORE_ID&business_date=2026-03-03" \
  -H "$AUTH"

# Create an operational ticket (exactly one Idempotency-Key; reuse it only to retry)
KEY="$(uuidgen)"
curl -s -X POST "$AGENTO_BASE_URL/api/v1/operations/tickets" -H "$AUTH" \
  -H "Idempotency-Key: $KEY" -H "Content-Type: application/json" \
  -d "{\"store_id\": \"$STORE_ID\", \"title\": \"Investigate failed shipment\", \"description\": \"Review the failed shipment.\"}"
# -> {"request_id": ..., "command_id": "<COMMAND_ID>", "status": "verified", "ticket_id": ..., "replayed": false, ...}

# Ticket command status (read-only)
curl -s "$AGENTO_BASE_URL/api/v1/operations/tickets/commands?command_id=$COMMAND_ID" -H "$AUTH"

# Employee Chat: create a thread (no model call)
curl -s -X POST "$AGENTO_BASE_URL/api/v1/chat/threads" -H "$AUTH" \
  -H "Content-Type: application/json" -d "{\"store_id\": \"$STORE_ID\"}"
# -> {"request_id": ..., "thread": {"thread_id": "<THREAD_ID>", ...}}

# Employee Chat: submit a turn (client turn_id makes it idempotent)
curl -s -X POST "$AGENTO_BASE_URL/api/v1/chat/turns" -H "$AUTH" \
  -H "Content-Type: application/json" \
  -d "{\"thread_id\": \"$THREAD_ID\", \"turn_id\": \"$(uuidgen)\", \"message\": \"Analyze operations for 2026-03-03.\"}"
# A turn that proposes a ticket answers "Ticket prepared. Confirm the action to create it."
# and returns "proposal": {"proposal_id": "<PROPOSAL_ID>", "state": "proposed", ...}

# Employee Chat: confirm a stored ticket proposal (only the id + one Idempotency-Key)
curl -s -X POST "$AGENTO_BASE_URL/api/v1/chat/ticket-proposals/confirm" -H "$AUTH" \
  -H "Idempotency-Key: $(uuidgen)" -H "Content-Type: application/json" \
  -d "{\"proposal_id\": \"$PROPOSAL_ID\"}"
# -> {"proposal": {"state": "submitted", ...}, "ticket": {"status": "verified", ...}}
```

<!-- BEGIN GENERATED ENDPOINT REFERENCE (apps/api/scripts/export_product_openapi.py) -->

## 11. Endpoint reference (generated)

Generated from the Product source with the OpenAPI artifact. Types link to the [schemas](#appendix-a-schemas-generated). `Auth: Product API key` means `Authorization: Bearer <Product API key>`.

### Health

#### `GET /health`

Public, legacy general health: status, application name, version, environment and agent-runtime status. No authentication.

- **Auth:** none (public)
- **Product permission:** none (public)
- **Request body:** none
- **Success:** `200` object

#### `GET /health/live`

Public liveness. Always `{"status": "alive"}`; touches no dependency and discloses nothing else.

- **Auth:** none (public)
- **Product permission:** none (public)
- **Request body:** none
- **Success:** `200` [`LivenessStatus`](#schema-livenessstatus): Healthy

#### `GET /health/ready`

Public readiness: `{"status": "ready"}` (200) or `{"status": "not_ready"}` (503). Never a reason, version or host.

- **Auth:** none (public)
- **Product permission:** none (public)
- **Request body:** none
- **Success:** `200` [`ReadinessStatus`](#schema-readinessstatus): Healthy
- **Errors:** `503` Not ready

### System

#### `GET /api/v1/system/status`

Read-only installation status: component states and stable not-ready reasons; never a URL, host, identifier or secret.

- **Auth:** Product API key
- **Product permission:** system.read
- **Request body:** none
- **Success:** `200` [`SystemStatusResponse`](#schema-systemstatusresponse)
- **Errors:** `401` Missing or invalid Product API key; `403` The actor lacks system.read; `503` System status unavailable

### Operations

#### `GET /api/v1/operations/reports/daily`

Deterministic daily report (no model). The store's own timezone decides the business day; `business_date` is optional (YYYY-MM-DD).

- **Auth:** Product API key
- **Product permission:** stores.read, orders.read, shipments.read
- **Query parameters:**
  - `store_id`: string (uuid), required
  - `business_date`: string (date) \| null, optional
- **Request body:** none
- **Success:** `200` [`DailyOperationsReportResponse`](#schema-dailyoperationsreportresponse)
- **Errors:** `401` Missing or invalid Product API key; `403` Forbidden: store not granted, or a required read permission is missing; `422` Request validation failed (values are never echoed); `503` Daily operations report unavailable

#### `POST /api/v1/operations/runs`

Runs the Operations Agent READ-ONLY on one granted store: it can never write. 409 when the Operations Agent is disabled (refused before any model or tool runs).

- **Auth:** Product API key
- **Product permission:** orders.read, shipments.read, stores.read (per tool, in the selected store)
- **Request body:** [`OperationsRunRequest`](#schema-operationsrunrequest)
- **Success:** `200` [`OperationsRunResponse`](#schema-operationsrunresponse)
- **Errors:** `401` Missing or invalid Product API key; `403` Forbidden: the store is not granted to the actor; `409` Operations Agent is disabled; `422` Request validation failed (values are never echoed); `503` Operations service unavailable

#### `POST /api/v1/operations/tickets`

The governed, durable ticket write (`operations.ticket.create`). Requires exactly one `Idempotency-Key`. HTTP status reports processing; only `status: verified` means the ticket was created.

- **Auth:** Product API key
- **Product permission:** tickets.create
- **Required headers:**
  - `Idempotency-Key`: string, required; min length `1`, max length `128`, pattern `^[A-Za-z0-9._:~-]{1,128}$`
- **Request body:** [`OperationsTicketRequest`](#schema-operationsticketrequest)
- **Success:** `200` [`OperationsTicketResponse`](#schema-operationsticketresponse): Processed: a replay of the same Idempotency-Key, or a denied/failed business outcome (see `status`)
- **Success:** `201` [`OperationsTicketResponse`](#schema-operationsticketresponse)
- **Success:** `202` [`OperationsTicketResponse`](#schema-operationsticketresponse): Accepted: in progress, awaiting approval, requires a human, or persistence incomplete. Not a created ticket
- **Errors:** `400` Idempotency-Key missing, sent more than once, or invalid; `401` Missing or invalid Product API key; `403` Forbidden: the store is not granted to the actor; `409` Idempotency conflict: the key was used for a different request; `422` Request validation failed (values are never echoed); `503` Operations ticket service unavailable

#### `GET /api/v1/operations/tickets/commands`

Read-only status of one durable ticket command of THIS principal in a currently granted store; anything else is the same 404. Never executes anything.

- **Auth:** Product API key
- **Product permission:** none beyond authentication
- **Query parameters:**
  - `command_id`: string (uuid), required
- **Request body:** none
- **Success:** `200` [`OperationsTicketCommandStatusResponse`](#schema-operationsticketcommandstatusresponse)
- **Errors:** `401` Missing or invalid Product API key; `404` Ticket command not found (unknown, another principal's, or a store not currently granted); `422` Request validation failed (values are never echoed); `503` Operations ticket query service unavailable

### Integrations

#### `GET /api/v1/integrations/catalog`

Installed integration types (this build installs none). Metadata only.

- **Auth:** Product API key
- **Product permission:** integrations.read
- **Request body:** none
- **Success:** `200` [`CatalogResponse`](#schema-catalogresponse)
- **Errors:** `401` Missing or invalid Product API key; `403` The actor lacks the required permission; `503` Integration management (or its secret storage) unavailable

#### `DELETE /api/v1/integrations/connection`

Remove a connection and its stored credentials.

- **Auth:** Product API key
- **Product permission:** integrations.manage
- **Query parameters:**
  - `connection_id`: string (uuid), required
- **Request body:** none
- **Success:** `200` [`ConnectionDeletedResponse`](#schema-connectiondeletedresponse)
- **Errors:** `401` Missing or invalid Product API key; `403` The actor lacks the required permission; `404` Unknown connection; `409` The operation did not complete (nothing confirmed); `422` Request validation failed (values are never echoed); `503` Integration management (or its secret storage) unavailable

#### `GET /api/v1/integrations/connection`

One connection by `connection_id`. Credential values are never returned.

- **Auth:** Product API key
- **Product permission:** integrations.read
- **Query parameters:**
  - `connection_id`: string (uuid), required
- **Request body:** none
- **Success:** `200` [`ConnectionResponse`](#schema-connectionresponse)
- **Errors:** `401` Missing or invalid Product API key; `403` The actor lacks the required permission; `404` Unknown connection; `422` Request validation failed (values are never echoed); `503` Integration management (or its secret storage) unavailable

#### `PUT /api/v1/integrations/connection`

Update a connection's display name and non-secret configuration.

- **Auth:** Product API key
- **Product permission:** integrations.manage
- **Query parameters:**
  - `connection_id`: string (uuid), required
- **Request body:** [`UpdateConnectionRequest`](#schema-updateconnectionrequest)
- **Success:** `200` [`ConnectionResponse`](#schema-connectionresponse)
- **Errors:** `401` Missing or invalid Product API key; `403` The actor lacks the required permission; `404` Unknown connection; `409` The operation did not complete (nothing confirmed); `422` Request validation failed (values are never echoed); `503` Integration management (or its secret storage) unavailable

#### `PUT /api/v1/integrations/connection/credentials`

Replace a connection's credentials explicitly. Values are never returned, logged or echoed.

- **Auth:** Product API key
- **Product permission:** integrations.manage
- **Query parameters:**
  - `connection_id`: string (uuid), required
- **Request body:** [`ReplaceCredentialsRequest`](#schema-replacecredentialsrequest)
- **Success:** `200` [`ConnectionResponse`](#schema-connectionresponse)
- **Errors:** `401` Missing or invalid Product API key; `403` The actor lacks the required permission; `404` Unknown connection; `409` The operation did not complete (nothing confirmed); `422` Request validation failed (values are never echoed); `503` Integration management (or its secret storage) unavailable

#### `POST /api/v1/integrations/connection/disable`

Disable a connection.

- **Auth:** Product API key
- **Product permission:** integrations.manage
- **Query parameters:**
  - `connection_id`: string (uuid), required
- **Request body:** none
- **Success:** `200` [`ConnectionResponse`](#schema-connectionresponse)
- **Errors:** `401` Missing or invalid Product API key; `403` The actor lacks the required permission; `404` Unknown connection; `409` The operation did not complete (nothing confirmed); `422` Request validation failed (values are never echoed); `503` Integration management (or its secret storage) unavailable

#### `POST /api/v1/integrations/connection/enable`

Enable a connection.

- **Auth:** Product API key
- **Product permission:** integrations.manage
- **Query parameters:**
  - `connection_id`: string (uuid), required
- **Request body:** none
- **Success:** `200` [`ConnectionResponse`](#schema-connectionresponse)
- **Errors:** `401` Missing or invalid Product API key; `403` The actor lacks the required permission; `404` Unknown connection; `409` The operation did not complete (nothing confirmed); `422` Request validation failed (values are never echoed); `503` Integration management (or its secret storage) unavailable

#### `POST /api/v1/integrations/connection/test`

Test a connection through its installed driver; the result is a stable code.

- **Auth:** Product API key
- **Product permission:** integrations.manage
- **Query parameters:**
  - `connection_id`: string (uuid), required
- **Request body:** none
- **Success:** `200` [`ConnectionResponse`](#schema-connectionresponse)
- **Errors:** `401` Missing or invalid Product API key; `403` The actor lacks the required permission; `404` Unknown connection; `409` The operation did not complete (nothing confirmed); `422` Request validation failed (values are never echoed); `503` Integration management (or its secret storage) unavailable

#### `GET /api/v1/integrations/connections`

This installation's connections. Credential values are never returned.

- **Auth:** Product API key
- **Product permission:** integrations.read
- **Request body:** none
- **Success:** `200` [`ConnectionListResponse`](#schema-connectionlistresponse)
- **Errors:** `401` Missing or invalid Product API key; `403` The actor lacks the required permission; `503` Integration management (or its secret storage) unavailable

#### `POST /api/v1/integrations/connections`

Create a connection. Credentials go straight to the secret store and are never returned or echoed. Connection management only; no data sync.

- **Auth:** Product API key
- **Product permission:** integrations.manage
- **Request body:** [`CreateConnectionRequest`](#schema-createconnectionrequest)
- **Success:** `201` [`ConnectionResponse`](#schema-connectionresponse)
- **Errors:** `401` Missing or invalid Product API key; `403` The actor lacks the required permission; `404` Unknown connection; `409` The operation did not complete (nothing confirmed); `422` Request validation failed (values are never echoed); `503` Integration management (or its secret storage) unavailable

### Agents

#### `GET /api/v1/agents`

Installed Agents with this installation's effective enable/disable state.

- **Auth:** Product API key
- **Product permission:** agents.read
- **Request body:** none
- **Success:** `200` [`AgentListResponse`](#schema-agentlistresponse)
- **Errors:** `401` Missing or invalid Product API key; `403` The actor lacks the required permission (Agents never have it); `503` Agent management unavailable

#### `GET /api/v1/agents/agent`

One Agent by `agent_id`.

- **Auth:** Product API key
- **Product permission:** agents.read
- **Query parameters:**
  - `agent_id`: string, required; min length `2`, max length `64`, pattern `^[a-z][a-z0-9-]{0,62}[a-z0-9]$`
- **Request body:** none
- **Success:** `200` [`ProductAgentResponse`](#schema-productagentresponse)
- **Errors:** `401` Missing or invalid Product API key; `403` The actor lacks the required permission (Agents never have it); `404` No such Product Agent is installed; `422` Request validation failed (values are never echoed); `503` Agent management unavailable

#### `DELETE /api/v1/agents/agent/configuration`

Reset an Agent to its Product default (removes this installation's override).

- **Auth:** Product API key
- **Product permission:** agents.manage
- **Query parameters:**
  - `agent_id`: string, required; min length `2`, max length `64`, pattern `^[a-z][a-z0-9-]{0,62}[a-z0-9]$`
- **Request body:** none
- **Success:** `200` [`ProductAgentResponse`](#schema-productagentresponse)
- **Errors:** `401` Missing or invalid Product API key; `403` The actor lacks the required permission (Agents never have it); `404` No such Product Agent is installed; `409` The operation did not complete (nothing confirmed); `422` Request validation failed (values are never echoed); `503` Agent management unavailable

#### `POST /api/v1/agents/agent/disable`

Disable an Agent: its runs (Operations runs, Employee Chat turns) are refused before it runs.

- **Auth:** Product API key
- **Product permission:** agents.manage
- **Query parameters:**
  - `agent_id`: string, required; min length `2`, max length `64`, pattern `^[a-z][a-z0-9-]{0,62}[a-z0-9]$`
- **Request body:** none
- **Success:** `200` [`ProductAgentResponse`](#schema-productagentresponse)
- **Errors:** `401` Missing or invalid Product API key; `403` The actor lacks the required permission (Agents never have it); `404` No such Product Agent is installed; `409` The operation did not complete (nothing confirmed); `422` Request validation failed (values are never echoed); `503` Agent management unavailable

#### `POST /api/v1/agents/agent/enable`

Enable an Agent for this installation. Cannot create Agents, edit instructions or choose models/tools.

- **Auth:** Product API key
- **Product permission:** agents.manage
- **Query parameters:**
  - `agent_id`: string, required; min length `2`, max length `64`, pattern `^[a-z][a-z0-9-]{0,62}[a-z0-9]$`
- **Request body:** none
- **Success:** `200` [`ProductAgentResponse`](#schema-productagentresponse)
- **Errors:** `401` Missing or invalid Product API key; `403` The actor lacks the required permission (Agents never have it); `404` No such Product Agent is installed; `409` The operation did not complete (nothing confirmed); `422` Request validation failed (values are never echoed); `503` Agent management unavailable

#### `GET /api/v1/agents/catalog`

The Product Agents installed in this build (manifests). Not AgentOS.

- **Auth:** Product API key
- **Product permission:** agents.read
- **Request body:** none
- **Success:** `200` [`AgentCatalogResponse`](#schema-agentcatalogresponse)
- **Errors:** `401` Missing or invalid Product API key; `403` The actor lacks the required permission (Agents never have it); `503` Agent management unavailable

### Skills

#### `GET /api/v1/skills/catalog`

Read-only Skill catalog (reviewed Product source). No install or run endpoint exists.

- **Auth:** Product API key
- **Product permission:** agents.read
- **Request body:** none
- **Success:** `200` [`SkillCatalogResponse`](#schema-skillcatalogresponse)
- **Errors:** `401` Missing or invalid Product API key; `403` The actor lacks `agents.read`; `503` Agent management unavailable

#### `GET /api/v1/skills/skill`

One Skill by `skill_id`.

- **Auth:** Product API key
- **Product permission:** agents.read
- **Query parameters:**
  - `skill_id`: string, required; max length `128`, pattern `^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$`
- **Request body:** none
- **Success:** `200` [`SkillResponse`](#schema-skillresponse)
- **Errors:** `401` Missing or invalid Product API key; `403` The actor lacks `agents.read`; `404` No such Skill/Task in this build; `422` Request validation failed (values are never echoed); `503` Agent management unavailable

### Tasks

#### `GET /api/v1/tasks/catalog`

Read-only Task catalog. There is no Task executor endpoint.

- **Auth:** Product API key
- **Product permission:** agents.read
- **Request body:** none
- **Success:** `200` [`TaskCatalogResponse`](#schema-taskcatalogresponse)
- **Errors:** `401` Missing or invalid Product API key; `403` The actor lacks `agents.read`; `503` Agent management unavailable

#### `GET /api/v1/tasks/task`

One Task by `task_id`.

- **Auth:** Product API key
- **Product permission:** agents.read
- **Query parameters:**
  - `task_id`: string, required; max length `128`, pattern `^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$`
- **Request body:** none
- **Success:** `200` [`TaskResponse`](#schema-taskresponse)
- **Errors:** `401` Missing or invalid Product API key; `403` The actor lacks `agents.read`; `404` No such Skill/Task in this build; `422` Request validation failed (values are never echoed); `503` Agent management unavailable

### Workflows

#### `GET /api/v1/workflows/catalog`

Read-only Workflow catalog. There is no public Workflow run endpoint.

- **Auth:** Product API key
- **Product permission:** workflows.read
- **Request body:** none
- **Success:** `200` [`WorkflowCatalogResponse`](#schema-workflowcatalogresponse)
- **Errors:** `401` Missing or invalid Product API key; `403` The actor lacks `workflows.read`; `503` Workflow inspection unavailable

#### `GET /api/v1/workflows/run`

One Workflow run with Step attempts and events (metadata only).

- **Auth:** Product API key
- **Product permission:** workflows.read
- **Query parameters:**
  - `run_id`: string (uuid), required
- **Request body:** none
- **Success:** `200` [`WorkflowRunResponse`](#schema-workflowrunresponse)
- **Errors:** `401` Missing or invalid Product API key; `403` The actor lacks `workflows.read`; `404` No such Workflow / no such run in your company (indistinguishable); `422` Request validation failed (values are never echoed); `503` Workflow inspection unavailable

#### `GET /api/v1/workflows/runs`

This company's Workflow run history (metadata only; never inputs, checkpoints or outputs).

- **Auth:** Product API key
- **Product permission:** workflows.read
- **Query parameters:**
  - `limit`: integer, optional; min `1`, max `100`, default `25`
- **Request body:** none
- **Success:** `200` [`WorkflowRunListResponse`](#schema-workflowrunlistresponse)
- **Errors:** `401` Missing or invalid Product API key; `403` The actor lacks `workflows.read`; `422` Request validation failed (values are never echoed); `503` Workflow inspection unavailable

#### `GET /api/v1/workflows/workflow`

One Workflow definition by `workflow_id`.

- **Auth:** Product API key
- **Product permission:** workflows.read
- **Query parameters:**
  - `workflow_id`: string, required; max length `128`, pattern `^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$`
- **Request body:** none
- **Success:** `200` [`WorkflowResponse`](#schema-workflowresponse)
- **Errors:** `401` Missing or invalid Product API key; `403` The actor lacks `workflows.read`; `404` No such Workflow / no such run in your company (indistinguishable); `422` Request validation failed (values are never echoed); `503` Workflow inspection unavailable

### Knowledge

#### `GET /api/v1/knowledge/document`

One document; another company's document is the same 404.

- **Auth:** Product API key
- **Product permission:** knowledge.read
- **Query parameters:**
  - `document_id`: string (uuid), required
- **Request body:** none
- **Success:** `200` [`DocumentResponse`](#schema-documentresponse)
- **Errors:** `401` Missing or invalid Product API key; `403` The actor lacks the required Knowledge permission; `404` No such document / version in your company (indistinguishable); `422` Request validation failed (values are never echoed); `503` Knowledge unavailable

#### `POST /api/v1/knowledge/document/archive`

Archive a document. There is no delete.

- **Auth:** Product API key
- **Product permission:** knowledge.manage
- **Query parameters:**
  - `document_id`: string (uuid), required
- **Request body:** none
- **Success:** `200` [`DocumentResponse`](#schema-documentresponse)
- **Errors:** `401` Missing or invalid Product API key; `403` The actor lacks the required Knowledge permission; `404` No such document / version in your company (indistinguishable); `409` The governed write did not complete (nothing confirmed); `422` Request validation failed (values are never echoed); `503` Knowledge unavailable

#### `POST /api/v1/knowledge/document/create`

Create a text document. No upload or URL import.

- **Auth:** Product API key
- **Product permission:** knowledge.manage
- **Request body:** [`CreateDocumentRequest`](#schema-createdocumentrequest)
- **Success:** `200` [`DocumentResponse`](#schema-documentresponse)
- **Errors:** `401` Missing or invalid Product API key; `403` The actor lacks the required Knowledge permission; `404` No such document / version in your company (indistinguishable); `409` The governed write did not complete (nothing confirmed); `422` Request validation failed (values are never echoed); `503` Knowledge unavailable

#### `GET /api/v1/knowledge/document/version`

One document version (text returned as untrusted data).

- **Auth:** Product API key
- **Product permission:** knowledge.read
- **Query parameters:**
  - `document_id`: string (uuid), required
  - `version`: integer, required; min `1`, max `1000000000`
- **Request body:** none
- **Success:** `200` [`DocumentVersionResponse`](#schema-documentversionresponse)
- **Errors:** `401` Missing or invalid Product API key; `403` The actor lacks the required Knowledge permission; `404` No such document / version in your company (indistinguishable); `422` Request validation failed (values are never echoed); `503` Knowledge unavailable

#### `POST /api/v1/knowledge/document/version`

Publish a new version of a document.

- **Auth:** Product API key
- **Product permission:** knowledge.manage
- **Query parameters:**
  - `document_id`: string (uuid), required
- **Request body:** [`PublishVersionRequest`](#schema-publishversionrequest)
- **Success:** `200` [`DocumentResponse`](#schema-documentresponse)
- **Errors:** `401` Missing or invalid Product API key; `403` The actor lacks the required Knowledge permission; `404` No such document / version in your company (indistinguishable); `409` The governed write did not complete (nothing confirmed); `422` Request validation failed (values are never echoed); `503` Knowledge unavailable

#### `GET /api/v1/knowledge/documents`

Knowledge documents of this company.

- **Auth:** Product API key
- **Product permission:** knowledge.read
- **Request body:** none
- **Success:** `200` [`DocumentListResponse`](#schema-documentlistresponse)
- **Errors:** `401` Missing or invalid Product API key; `403` The actor lacks the required Knowledge permission; `503` Knowledge unavailable

#### `GET /api/v1/knowledge/operating-model`

The current company operating model (versioned).

- **Auth:** Product API key
- **Product permission:** knowledge.read
- **Request body:** none
- **Success:** `200` [`OperatingModelResponse`](#schema-operatingmodelresponse)
- **Errors:** `401` Missing or invalid Product API key; `403` The actor lacks the required Knowledge permission; `503` Knowledge unavailable

#### `POST /api/v1/knowledge/operating-model/publish`

Publish a new operating-model version. A submitted `company_id` is refused.

- **Auth:** Product API key
- **Product permission:** knowledge.manage
- **Request body:** [`PublishOperatingModelRequest`](#schema-publishoperatingmodelrequest)
- **Success:** `200` [`OperatingModelResponse`](#schema-operatingmodelresponse)
- **Errors:** `401` Missing or invalid Product API key; `403` The actor lacks the required Knowledge permission; `404` No such document / version in your company (indistinguishable); `409` The governed write did not complete (nothing confirmed); `422` Request validation failed (values are never echoed); `503` Knowledge unavailable

#### `GET /api/v1/knowledge/operating-model/version`

One operating-model version.

- **Auth:** Product API key
- **Product permission:** knowledge.read
- **Query parameters:**
  - `version`: integer, required; min `1`, max `1000000000`
- **Request body:** none
- **Success:** `200` [`OperatingModelResponse`](#schema-operatingmodelresponse)
- **Errors:** `401` Missing or invalid Product API key; `403` The actor lacks the required Knowledge permission; `404` No such document / version in your company (indistinguishable); `422` Request validation failed (values are never echoed); `503` Knowledge unavailable

#### `GET /api/v1/knowledge/operating-model/versions`

Operating-model version history.

- **Auth:** Product API key
- **Product permission:** knowledge.read
- **Request body:** none
- **Success:** `200` [`OperatingModelVersionsResponse`](#schema-operatingmodelversionsresponse)
- **Errors:** `401` Missing or invalid Product API key; `403` The actor lacks the required Knowledge permission; `503` Knowledge unavailable

#### `POST /api/v1/knowledge/query`

Bounded, company-scoped Knowledge retrieval. Results are untrusted reference data.

- **Auth:** Product API key
- **Product permission:** knowledge.read
- **Request body:** [`QueryRequest`](#schema-queryrequest)
- **Success:** `200` [`QueryResponse`](#schema-queryresponse)
- **Errors:** `401` Missing or invalid Product API key; `403` The actor lacks the required Knowledge permission; `404` No such document / version in your company (indistinguishable); `422` Request validation failed (values are never echoed); `503` Knowledge unavailable

### Approvals

#### `GET /api/v1/approvals`

Approval requests. There is NO create endpoint: requests come only from governance.

- **Auth:** Product API key
- **Product permission:** approvals.read
- **Query parameters:**
  - `status`: [`ApprovalStatus`](#schema-approvalstatus) \| null, optional
  - `action_name`: string \| null, optional
  - `limit`: integer, optional; min `1`, max `100`, default `50`
- **Request body:** none
- **Success:** `200` [`ApprovalListResponse`](#schema-approvallistresponse)
- **Errors:** `401` Missing or invalid Product API key; `403` The actor lacks the required approvals permission; `404` No such request in your company (indistinguishable); `422` Request validation failed (values are never echoed); `503` Approvals unavailable

#### `GET /api/v1/approvals/approval`

One approval request with its append-only events.

- **Auth:** Product API key
- **Product permission:** approvals.read
- **Query parameters:**
  - `approval_id`: string (uuid), required
- **Request body:** none
- **Success:** `200` [`ApprovalResponse`](#schema-approvalresponse)
- **Errors:** `401` Missing or invalid Product API key; `403` The actor lacks the required approvals permission; `404` No such request in your company (indistinguishable); `422` Request validation failed (values are never echoed); `503` Approvals unavailable

#### `POST /api/v1/approvals/approval/approve`

Approve a request (the requester cannot decide their own request).

- **Auth:** Product API key
- **Product permission:** approvals.decide
- **Query parameters:**
  - `approval_id`: string (uuid), required
- **Request body:** [`DecisionRequest`](#schema-decisionrequest) \| null
- **Success:** `200` [`ApprovalResponse`](#schema-approvalresponse)
- **Errors:** `401` Missing or invalid Product API key; `403` The actor lacks the required approvals permission; `404` No such request in your company (indistinguishable); `409` The request is no longer pending (decided, expired or a race lost); `422` Request validation failed (values are never echoed); `503` Approvals unavailable

#### `POST /api/v1/approvals/approval/cancel`

Cancel a pending request.

- **Auth:** Product API key
- **Product permission:** approvals.cancel
- **Query parameters:**
  - `approval_id`: string (uuid), required
- **Request body:** [`DecisionRequest`](#schema-decisionrequest)
- **Success:** `200` [`ApprovalResponse`](#schema-approvalresponse)
- **Errors:** `401` Missing or invalid Product API key; `403` The actor lacks the required approvals permission; `404` No such request in your company (indistinguishable); `409` The request is no longer pending (decided, expired or a race lost); `422` Request validation failed (values are never echoed); `503` Approvals unavailable

#### `POST /api/v1/approvals/approval/reject`

Reject a request.

- **Auth:** Product API key
- **Product permission:** approvals.decide
- **Query parameters:**
  - `approval_id`: string (uuid), required
- **Request body:** [`DecisionRequest`](#schema-decisionrequest)
- **Success:** `200` [`ApprovalResponse`](#schema-approvalresponse)
- **Errors:** `401` Missing or invalid Product API key; `403` The actor lacks the required approvals permission; `404` No such request in your company (indistinguishable); `409` The request is no longer pending (decided, expired or a race lost); `422` Request validation failed (values are never echoed); `503` Approvals unavailable

#### `POST /api/v1/approvals/approval/resume-workflow`

Explicitly continue the Workflow paused by an approved request.

- **Auth:** Product API key
- **Product permission:** approvals.read (the requester)
- **Query parameters:**
  - `approval_id`: string (uuid), required
- **Request body:** none
- **Success:** `200` [`WorkflowResumeResponse`](#schema-workflowresumeresponse)
- **Errors:** `401` Missing or invalid Product API key; `403` The actor lacks the required approvals permission; `404` No such request in your company (indistinguishable); `409` The request is no longer pending (decided, expired or a race lost); `422` Request validation failed (values are never echoed); `503` Approvals unavailable

### Conversations

#### `GET /api/v1/conversations`

Canonical conversations (READ-ONLY). There is no ingest, webhook, send or reply endpoint.

- **Auth:** Product API key
- **Product permission:** conversations.read
- **Query parameters:**
  - `limit`: integer, optional; min `1`, max `100`, default `50`
  - `connection_id`: string (uuid) \| null, optional
- **Request body:** none
- **Success:** `200` [`ConversationListResponse`](#schema-conversationlistresponse)
- **Errors:** `401` Missing or invalid Product API key; `403` The actor lacks conversations.read; `422` Request validation failed (values are never echoed); `503` Conversations unavailable

#### `GET /api/v1/conversations/conversation`

One conversation; one of another company or an inaccessible store is the same 404.

- **Auth:** Product API key
- **Product permission:** conversations.read
- **Query parameters:**
  - `conversation_id`: string (uuid), required
- **Request body:** none
- **Success:** `200` [`ConversationResponse`](#schema-conversationresponse)
- **Errors:** `401` Missing or invalid Product API key; `403` The actor lacks conversations.read; `404` No such conversation visible to you (indistinguishable); `422` Request validation failed (values are never echoed); `503` Conversations unavailable

#### `GET /api/v1/conversations/messages`

A page of a conversation's messages. Message text is untrusted external data.

- **Auth:** Product API key
- **Product permission:** conversations.read
- **Query parameters:**
  - `conversation_id`: string (uuid), required
  - `before_sequence`: integer \| null, optional
  - `limit`: integer, optional; min `1`, max `100`, default `50`
- **Request body:** none
- **Success:** `200` [`MessageListResponse`](#schema-messagelistresponse)
- **Errors:** `401` Missing or invalid Product API key; `403` The actor lacks conversations.read; `404` No such conversation visible to you (indistinguishable); `422` Request validation failed (values are never echoed); `503` Conversations unavailable

### Employee Chat

#### `GET /api/v1/chat/thread`

Employee Chat: one own thread with its turns and ticket proposals. Another actor's or company's thread is the same 404.

- **Auth:** Product API key
- **Product permission:** none beyond authentication
- **Query parameters:**
  - `thread_id`: string (uuid), required
- **Request body:** none
- **Success:** `200` [`ThreadDetailResponse`](#schema-threaddetailresponse)
- **Errors:** `401` Missing or invalid Product API key; `404` Chat thread not found (also another actor's or company's, or a store no longer granted); `422` Request validation failed (values are never echoed); `503` Employee chat unavailable

#### `GET /api/v1/chat/threads`

Employee Chat: the caller's own threads in one granted store (newest first, at most 50). Never calls the model.

- **Auth:** Product API key
- **Product permission:** none beyond authentication
- **Query parameters:**
  - `store_id`: string (uuid), required
- **Request body:** none
- **Success:** `200` [`ThreadsResponse`](#schema-threadsresponse)
- **Errors:** `401` Missing or invalid Product API key; `403` Forbidden: the store is not granted to the actor; `422` Request validation failed (values are never echoed); `503` Employee chat unavailable

#### `POST /api/v1/chat/threads`

Employee Chat: create an empty thread in a granted store. Never calls the model.

- **Auth:** Product API key
- **Product permission:** none beyond authentication
- **Request body:** [`CreateThreadRequest`](#schema-createthreadrequest)
- **Success:** `201` [`ThreadResponse`](#schema-threadresponse)
- **Errors:** `401` Missing or invalid Product API key; `403` Forbidden: the store is not granted to the actor; `422` Request validation failed (values are never echoed); `503` Employee chat unavailable

#### `POST /api/v1/chat/ticket-proposals/cancel`

Cancel a stored proposal (terminal). A confirmed proposal cannot be cancelled (409).

- **Auth:** Product API key
- **Product permission:** none beyond authentication
- **Request body:** [`ProposalRequest`](#schema-proposalrequest)
- **Success:** `200` [`CancelResponse`](#schema-cancelresponse)
- **Errors:** `401` Missing or invalid Product API key; `404` Ticket proposal not found; `409` Ticket proposal was already confirmed; `422` Request validation failed (values are never echoed); `503` Employee chat unavailable

#### `POST /api/v1/chat/ticket-proposals/confirm`

Explicit human confirmation of a STORED proposal: send only `proposal_id` and exactly one `Idempotency-Key`, never title, description, action or store. Runs the governed ticket WriteCommand; same-key retry replays it; another key is 409.

- **Auth:** Product API key
- **Product permission:** tickets.create
- **Required headers:**
  - `Idempotency-Key`: string, required; min length `1`, max length `128`, pattern `^[A-Za-z0-9._:~-]{1,128}$`
- **Request body:** [`ProposalRequest`](#schema-proposalrequest)
- **Success:** `200` [`ConfirmResponse`](#schema-confirmresponse): Processed: a replay of the same Idempotency-Key, or a denied/failed business outcome (see `status`)
- **Success:** `201` [`ConfirmResponse`](#schema-confirmresponse): Ticket command processed and verified (see `ticket.status`)
- **Success:** `202` [`ConfirmResponse`](#schema-confirmresponse): Accepted: in progress, awaiting approval, requires a human, or persistence incomplete. Not a created ticket
- **Errors:** `400` Idempotency-Key missing, sent more than once, or invalid; `401` Missing or invalid Product API key; `404` Ticket proposal not found (also another actor's or company's, or a store no longer granted); `409` Ticket proposal was cancelled, was already confirmed with another key, or idempotency conflict; `422` Request validation failed (values are never echoed); `503` Employee chat unavailable

#### `GET /api/v1/chat/turns`

Employee Chat: the turns and proposals of one own thread.

- **Auth:** Product API key
- **Product permission:** none beyond authentication
- **Query parameters:**
  - `thread_id`: string (uuid), required
- **Request body:** none
- **Success:** `200` [`TurnsResponse`](#schema-turnsresponse)
- **Errors:** `401` Missing or invalid Product API key; `404` Chat thread not found (also another actor's or company's, or a store no longer granted); `422` Request validation failed (values are never echoed); `503` Employee chat unavailable

#### `POST /api/v1/chat/turns`

Employee Chat: one idempotent turn (client `turn_id`) with the Operations Agent. Replay of a completed turn returns the stored answer without a model call. The Agent may only PROPOSE `operations.ticket.create`; a proposal turn's answer is always the Product-owned `Ticket prepared. Confirm the action to create it.` 409 when the Agent is disabled.

- **Auth:** Product API key
- **Product permission:** tool permissions of the Operations Agent (orders.read, shipments.read, stores.read)
- **Request body:** [`TurnRequest`](#schema-turnrequest)
- **Success:** `200` [`TurnResponse`](#schema-turnresponse): Replay of a completed turn (same turn_id and message): the stored answer, no model call
- **Success:** `201` [`TurnResponse`](#schema-turnresponse)
- **Errors:** `401` Missing or invalid Product API key; `404` Chat thread not found (also another actor's or company's, or a store no longer granted); `409` Operations Agent is disabled, chat turn conflict (same turn_id, different message or thread), or chat turn in progress; `422` Request validation failed (values are never echoed); `503` Employee chat unavailable

## Appendix A. Schemas (generated)

Every request and response model of the Product API, from the Pydantic source. **Strict** marks a model that rejects unknown fields.

<a id="schema-acceptancecriterionview"></a>
#### `AcceptanceCriterionView`

Strict: unknown fields are rejected.

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `code` | string | yes |  |
| `description` | string | yes |  |

<a id="schema-agentavailability"></a>
#### `AgentAvailability`

Enum: `available`, `disabled`, `unavailable`

<a id="schema-agentavailabilityreason"></a>
#### `AgentAvailabilityReason`

Enum: `disabled_by_configuration`, `runtime_not_composed`

<a id="schema-agentcatalogresponse"></a>
#### `AgentCatalogResponse`

Strict: unknown fields are rejected.

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `request_id` | string (uuid) | yes |  |
| `agents` | array of [`AgentDefinitionView`](#schema-agentdefinitionview) | yes |  |

<a id="schema-agentcategory"></a>
#### `AgentCategory`

Enum: `operations`

<a id="schema-agentdefinitionview"></a>
#### `AgentDefinitionView`

Strict: unknown fields are rejected.

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `agent_id` | string | yes |  |
| `name` | string | yes |  |
| `description` | string | yes |  |
| `category` | [`AgentCategory`](#schema-agentcategory) | yes |  |
| `lifecycle` | [`AgentLifecycle`](#schema-agentlifecycle) | yes |  |
| `default_enabled` | boolean | yes |  |
| `capabilities` | array of string | yes |  |
| `manifest` | [`AgentManifestView`](#schema-agentmanifestview) | yes |  |
| `skill_ids` | array of string | yes | Product Skills this Agent possesses (resolve them in the Skill catalog). |
| `task_ids` | array of string | yes | Product Tasks this Agent supports (resolve them in the Task catalog). |

<a id="schema-agentlifecycle"></a>
#### `AgentLifecycle`

Enum: `active`, `preview`, `deprecated`

<a id="schema-agentlistresponse"></a>
#### `AgentListResponse`

Strict: unknown fields are rejected.

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `request_id` | string (uuid) | yes |  |
| `agents` | array of [`AgentView`](#schema-agentview) | yes |  |

<a id="schema-agentmanifestview"></a>
#### `AgentManifestView`

Strict: unknown fields are rejected.

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `tools` | array of [`AgentToolView`](#schema-agenttoolview) | yes |  |
| `action_names` | array of string | yes |  |
| `tool_call_limit` | integer | yes |  |
| `requirements` | array of string | yes | Product domain capabilities required (never a provider). |
| `safety` | array of [`AgentSafetyProperty`](#schema-agentsafetyproperty) | yes |  |

<a id="schema-agentsafetyproperty"></a>
#### `AgentSafetyProperty`

Enum: `trusted_run_context`, `store_scoped`, `governed_tools`, `write_intent_required`, `untrusted_model`, `tool_output_untrusted`, `product_run_read_only`, `no_memory_knowledge_or_history`, `not_exposed_through_agentos`

<a id="schema-agentstateview"></a>
#### `AgentStateView`

Strict: unknown fields are rejected.

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `enabled` | boolean | yes | The configured state (override or Product default). |
| `source` | [`ConfigurationSource`](#schema-configurationsource) | yes |  |
| `availability` | [`AgentAvailability`](#schema-agentavailability) | yes | available: may run now; disabled: turned off by configuration; unavailable: enabled but its runtime is not composed in this deployment. |
| `reason` | [`AgentAvailabilityReason`](#schema-agentavailabilityreason) \| null | yes |  |
| `updated_at` | string (date-time) \| null | yes | When the override last changed (None: Product default). |

<a id="schema-agenttoolaccess"></a>
#### `AgentToolAccess`

Enum: `read`, `write`

<a id="schema-agenttoolview"></a>
#### `AgentToolView`

Strict: unknown fields are rejected.

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `tool_id` | string | yes |  |
| `access` | [`AgentToolAccess`](#schema-agenttoolaccess) | yes |  |
| `action_names` | array of string | yes | Governed Product actions behind the tool. |
| `description` | string | yes |  |

<a id="schema-agentview"></a>
#### `AgentView`

Strict: unknown fields are rejected.

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `definition` | [`AgentDefinitionView`](#schema-agentdefinitionview) | yes |  |
| `state` | [`AgentStateView`](#schema-agentstateview) | yes |  |

<a id="schema-applicationview"></a>
#### `ApplicationView`

Strict: unknown fields are rejected.

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `version` | string | yes | The Agento Product version. |
| `environment` | `local` \| `test` \| `staging` \| `production` | yes |  |
| `uptime_seconds` | integer | yes | min `0.0`; Seconds since this process started serving. |

<a id="schema-approvaleventtype"></a>
#### `ApprovalEventType`

Enum: `requested`, `approved`, `rejected`, `expired`, `cancelled`, `execution_claimed`, `execution_completed`

<a id="schema-approvallistresponse"></a>
#### `ApprovalListResponse`

Strict: unknown fields are rejected.

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `request_id` | string (uuid) | yes |  |
| `approvals` | array of [`ApprovalView`](#schema-approvalview) | yes | Newest first. |

<a id="schema-approvalresponse"></a>
#### `ApprovalResponse`

Strict: unknown fields are rejected.

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `request_id` | string (uuid) | yes |  |
| `approval` | [`ApprovalView`](#schema-approvalview) | yes |  |
| `events` | array of [`EventView`](#schema-eventview) | yes | Append-only lifecycle history. |

<a id="schema-approvalstatus"></a>
#### `ApprovalStatus`

Enum: `requested`, `approved`, `rejected`, `expired`, `cancelled`

<a id="schema-approvalview"></a>
#### `ApprovalView`

Strict: unknown fields are rejected.

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `approval_id` | string (uuid) | yes |  |
| `action_name` | string | yes |  |
| `risk` | string | yes |  |
| `status` | [`ApprovalStatus`](#schema-approvalstatus) | yes |  |
| `requester_actor_id` | string | yes |  |
| `requester_actor_type` | string | yes |  |
| `store_id` | string \| null | yes |  |
| `created_at` | string (date-time) | yes |  |
| `expires_at` | string (date-time) | yes |  |
| `decided_at` | string (date-time) \| null | yes |  |
| `decided_by_actor_id` | string \| null | yes |  |
| `decided_by_actor_type` | string \| null | yes |  |
| `decision_note` | string \| null | yes | Inert human text (never instructions). |
| `summary` | [`SummaryView`](#schema-summaryview) | yes | Trusted display only; never what is approved. |
| `source` | [`SourceView`](#schema-sourceview) | yes |  |
| `action_run_id` | string (uuid) | yes | The run that requested the decision. |
| `consumed` | boolean | yes |  |
| `consumed_by_action_run_id` | string (uuid) \| null | yes |  |
| `execution_outcome` | string \| null | yes |  |

<a id="schema-cancelresponse"></a>
#### `CancelResponse`

Strict: unknown fields are rejected.

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `request_id` | string (uuid) | yes |  |
| `proposal` | [`ProposalOut`](#schema-proposalout) | yes |  |

<a id="schema-catalogresponse"></a>
#### `CatalogResponse`

Strict: unknown fields are rejected.

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `request_id` | string (uuid) | yes |  |
| `integrations` | array of [`IntegrationDefinitionView`](#schema-integrationdefinitionview) | yes |  |

<a id="schema-changeview"></a>
#### `ChangeView`

Strict: unknown fields are rejected.

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `code` | string | yes |  |
| `label` | string | yes |  |
| `before` | string \| null | yes |  |
| `after` | string \| null | yes |  |

<a id="schema-channelview"></a>
#### `ChannelView`

Strict: unknown fields are rejected.

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `connection_id` | string (uuid) | yes |  |
| `integration_id` | string | yes |  |
| `integration_name` | string \| null | yes | None: not installed in this build. |
| `connection_name` | string \| null | yes | None: the connection was removed. |

<a id="schema-checkpointpolicy"></a>
#### `CheckpointPolicy`

Enum: `none`, `state`

<a id="schema-componentstate"></a>
#### `ComponentState`

Enum: `ready`, `starting`, `unavailable`, `mismatch`

<a id="schema-componentsview"></a>
#### `ComponentsView`

Strict: unknown fields are rejected.

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `application` | [`ComponentState`](#schema-componentstate) | yes |  |
| `database` | [`ComponentState`](#schema-componentstate) | yes |  |
| `product_schema` | [`ComponentState`](#schema-componentstate) | yes |  |
| `agent_runtime` | [`ComponentState`](#schema-componentstate) | yes |  |

<a id="schema-configfieldkind"></a>
#### `ConfigFieldKind`

Enum: `text`, `url`, `boolean`, `secret`

<a id="schema-configfieldview"></a>
#### `ConfigFieldView`

Strict: unknown fields are rejected.

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `name` | string | yes |  |
| `label` | string | yes |  |
| `kind` | [`ConfigFieldKind`](#schema-configfieldkind) | yes |  |
| `required` | boolean | yes |  |
| `help_text` | string \| null | yes |  |

<a id="schema-configurationsource"></a>
#### `ConfigurationSource`

Enum: `default`, `override`

<a id="schema-confirmresponse"></a>
#### `ConfirmResponse`

Strict: unknown fields are rejected.

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `request_id` | string (uuid) | yes |  |
| `proposal` | [`ProposalOut`](#schema-proposalout) | yes |  |
| `ticket` | [`TicketOut`](#schema-ticketout) | yes |  |

<a id="schema-connectiondeletedresponse"></a>
#### `ConnectionDeletedResponse`

Strict: unknown fields are rejected.

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `request_id` | string (uuid) | yes |  |
| `connection_id` | string (uuid) | yes |  |
| `deleted` | boolean | yes |  |

<a id="schema-connectionerrorcode"></a>
#### `ConnectionErrorCode`

Enum: `authentication_failed`, `permission_denied`, `unreachable`, `timeout`, `invalid_configuration`, `credentials_unavailable`, `unexpected_response`, `provider_error`

<a id="schema-connectionlistresponse"></a>
#### `ConnectionListResponse`

Strict: unknown fields are rejected.

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `request_id` | string (uuid) | yes |  |
| `connections` | array of [`ConnectionView`](#schema-connectionview) | yes |  |

<a id="schema-connectionresponse"></a>
#### `ConnectionResponse`

Strict: unknown fields are rejected.

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `request_id` | string (uuid) | yes |  |
| `connection` | [`ConnectionView`](#schema-connectionview) | yes |  |

<a id="schema-connectiontestresult"></a>
#### `ConnectionTestResult`

Enum: `never_tested`, `success`, `failure`

<a id="schema-connectionview"></a>
#### `ConnectionView`

Strict: unknown fields are rejected.

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `connection_id` | string (uuid) | yes |  |
| `integration_id` | string | yes |  |
| `display_name` | string | yes |  |
| `config` | object of string \| boolean | yes | Non-secret configuration only. |
| `configured_secret_fields` | array of string | yes | NAMES of the configured secret fields. Values are never returned. |
| `enabled` | boolean | yes |  |
| `created_at` | string (date-time) | yes |  |
| `updated_at` | string (date-time) | yes |  |
| `last_tested_at` | string (date-time) \| null | yes | When the last test ran (None: never). |
| `last_test_result` | [`ConnectionTestResult`](#schema-connectiontestresult) | yes | Last-KNOWN connectivity at last_tested_at, not a live status. |
| `last_test_error` | [`ConnectionErrorCode`](#schema-connectionerrorcode) \| null | yes |  |

<a id="schema-contenttype"></a>
#### `ContentType`

Enum: `text/plain`, `text/markdown`

<a id="schema-contextauthority"></a>
#### `ContextAuthority`

Enum: `security_permissions_policy`, `product_runtime_contracts`, `structured_operating_model`, `knowledge_document_references`

<a id="schema-conversationlistresponse"></a>
#### `ConversationListResponse`

Strict: unknown fields are rejected.

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `request_id` | string (uuid) | yes |  |
| `conversations` | array of [`ConversationView`](#schema-conversationview) | yes | Newest activity first. |

<a id="schema-conversationresponse"></a>
#### `ConversationResponse`

Strict: unknown fields are rejected.

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `request_id` | string (uuid) | yes |  |
| `conversation` | [`ConversationView`](#schema-conversationview) | yes |  |

<a id="schema-conversationview"></a>
#### `ConversationView`

Strict: unknown fields are rejected.

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `conversation_id` | string (uuid) | yes |  |
| `store_id` | string \| null | yes |  |
| `external_conversation_ref` | string | yes | Opaque external reference. |
| `channel` | [`ChannelView`](#schema-channelview) | yes |  |
| `created_at` | string (date-time) | yes |  |
| `last_message_at` | string (date-time) | yes |  |
| `last_message_id` | string (uuid) \| null | yes |  |

<a id="schema-createconnectionrequest"></a>
#### `CreateConnectionRequest`

Strict: unknown fields are rejected.

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `integration_id` | string | yes | pattern `^[a-z0-9][a-z0-9-]{0,62}[a-z0-9]$` |
| `display_name` | string | yes | min length `1`, max length `120` |
| `config` | object | no |  |
| `credentials` | object | no | Secret field values. Stored only in the integration secret store; never returned. |

<a id="schema-createdocumentrequest"></a>
#### `CreateDocumentRequest`

Strict: unknown fields are rejected.

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `title` | string | yes | max length `1000` |
| `content_type` | string | yes | max length `64`; text/plain or text/markdown |
| `body` | string | yes | max length `100000` |
| `category` | string | yes | max length `64` |

<a id="schema-createthreadrequest"></a>
#### `CreateThreadRequest`

Strict: unknown fields are rejected.

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `store_id` | string (uuid) | yes |  |

<a id="schema-dailyoperationscoverage"></a>
#### `DailyOperationsCoverage`

Strict: unknown fields are rejected.

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `orders` | [`OrdersCoverage`](#schema-orderscoverage) | no | default `"created_in_business_day"` |
| `shipments` | [`ShipmentsCoverage`](#schema-shipmentscoverage) | no | default `"shipped_in_business_day"` |
| `inventory` | [`InventoryCoverage`](#schema-inventorycoverage) | no | default `"not_included"` |
| `inventory_reason` | [`InventoryCoverageReason`](#schema-inventorycoveragereason) | no | default `"store_scoped_inventory_query_unavailable"` |

<a id="schema-dailyoperationsfinding"></a>
#### `DailyOperationsFinding`

Strict: unknown fields are rejected.

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `code` | [`FindingCode`](#schema-findingcode) | yes |  |
| `severity` | [`FindingSeverity`](#schema-findingseverity) | yes |  |
| `entity_type` | [`FindingEntityType`](#schema-findingentitytype) | yes |  |
| `entity_id` | string (uuid) | yes |  |
| `order_id` | string (uuid) | yes |  |
| `canonical_status` | string | yes |  |
| `recommended_action` | [`RecommendedAction`](#schema-recommendedaction) | yes |  |

<a id="schema-dailyoperationsmetrics"></a>
#### `DailyOperationsMetrics`

Strict: unknown fields are rejected.

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `orders_created` | integer | yes | min `0.0` |
| `order_status_counts` | array of [`OrderStatusCount`](#schema-orderstatuscount) | yes |  |
| `shipments_shipped` | integer | yes | min `0.0` |
| `shipment_status_counts` | array of [`ShipmentStatusCount`](#schema-shipmentstatuscount) | yes |  |
| `affected_orders` | integer | yes | min `0.0` |

<a id="schema-dailyoperationsreport"></a>
#### `DailyOperationsReport`

Strict: unknown fields are rejected.

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `store_id` | string (uuid) | yes |  |
| `business_date` | string (date) | yes |  |
| `timezone` | string | yes |  |
| `window_start` | string (date-time) | yes |  |
| `window_end` | string (date-time) | yes |  |
| `generated_at` | string (date-time) | yes |  |
| `metrics` | [`DailyOperationsMetrics`](#schema-dailyoperationsmetrics) | yes |  |
| `findings` | array of [`DailyOperationsFinding`](#schema-dailyoperationsfinding) | yes |  |
| `findings_total` | integer | yes | min `0.0` |
| `findings_truncated` | boolean | yes |  |
| `coverage` | [`DailyOperationsCoverage`](#schema-dailyoperationscoverage) | yes |  |

<a id="schema-dailyoperationsreportresponse"></a>
#### `DailyOperationsReportResponse`

Strict: unknown fields are rejected.

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `request_id` | string (uuid) | yes |  |
| `report` | [`DailyOperationsReport`](#schema-dailyoperationsreport) | yes |  |

<a id="schema-decisionrequest"></a>
#### `DecisionRequest`

Strict: unknown fields are rejected.

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `note` | string \| null | no | Approve: optional. Reject/cancel: required. |

<a id="schema-documentcontentview"></a>
#### `DocumentContentView`

Strict: unknown fields are rejected.

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `version` | integer | yes |  |
| `title` | string | yes |  |
| `content_type` | [`ContentType`](#schema-contenttype) | yes |  |
| `body` | string | yes | Text data (untrusted reference); never HTML or instructions. |
| `content_hash` | string | yes |  |
| `created_at` | string (date-time) | yes |  |
| `trust` | [`TrustClassification`](#schema-trustclassification) | no | default `"untrusted_reference"` |

<a id="schema-documentlifecycle"></a>
#### `DocumentLifecycle`

Enum: `active`, `archived`

<a id="schema-documentlistresponse"></a>
#### `DocumentListResponse`

Strict: unknown fields are rejected.

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `request_id` | string (uuid) | yes |  |
| `documents` | array of [`DocumentView`](#schema-documentview) | yes | Most recently updated first; archived documents included. |

<a id="schema-documentresponse"></a>
#### `DocumentResponse`

Strict: unknown fields are rejected.

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `request_id` | string (uuid) | yes |  |
| `document` | [`DocumentView`](#schema-documentview) | yes |  |
| `current` | [`DocumentContentView`](#schema-documentcontentview) | yes |  |
| `versions` | array of [`DocumentVersionView`](#schema-documentversionview) | yes | Immutable history, newest first. |

<a id="schema-documentversionresponse"></a>
#### `DocumentVersionResponse`

Strict: unknown fields are rejected.

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `request_id` | string (uuid) | yes |  |
| `document_id` | string (uuid) | yes |  |
| `version` | [`DocumentContentView`](#schema-documentcontentview) | yes |  |

<a id="schema-documentversionview"></a>
#### `DocumentVersionView`

Strict: unknown fields are rejected.

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `version` | integer | yes |  |
| `title` | string | yes |  |
| `content_type` | [`ContentType`](#schema-contenttype) | yes |  |
| `content_hash` | string | yes |  |
| `created_at` | string (date-time) | yes |  |

<a id="schema-documentview"></a>
#### `DocumentView`

Strict: unknown fields are rejected.

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `document_id` | string (uuid) | yes |  |
| `category` | [`KnowledgeCategory`](#schema-knowledgecategory) | yes |  |
| `lifecycle` | [`DocumentLifecycle`](#schema-documentlifecycle) | yes |  |
| `current_version` | integer | yes |  |
| `title` | string | yes |  |
| `created_at` | string (date-time) | yes |  |
| `updated_at` | string (date-time) | yes |  |

<a id="schema-errordetail"></a>
#### `ErrorDetail`

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `detail` | string | yes | A fixed message; never a submitted value. |

<a id="schema-eventview"></a>
#### `EventView`

Strict: unknown fields are rejected.

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `sequence` | integer | yes |  |
| `event_type` | [`ApprovalEventType`](#schema-approvaleventtype) | yes |  |
| `status` | [`ApprovalStatus`](#schema-approvalstatus) | yes |  |
| `actor_id` | string \| null | yes |  |
| `actor_type` | string \| null | yes |  |
| `action_run_id` | string (uuid) \| null | yes |  |
| `execution_outcome` | string \| null | yes |  |
| `occurred_at` | string (date-time) | yes |  |

<a id="schema-findingcode"></a>
#### `FindingCode`

Enum: `order_status_unknown`, `shipment_failed`, `shipment_returned`, `shipment_status_unknown`

<a id="schema-findingentitytype"></a>
#### `FindingEntityType`

Enum: `order`, `shipment`

<a id="schema-findingseverity"></a>
#### `FindingSeverity`

Enum: `critical`, `warning`

<a id="schema-integrationauthmode"></a>
#### `IntegrationAuthMode`

Enum: `none`, `credentials`, `delegated`

<a id="schema-integrationcategory"></a>
#### `IntegrationCategory`

Enum: `commerce`, `messaging`, `marketing`, `shipping`, `accounting`

<a id="schema-integrationdefinitionview"></a>
#### `IntegrationDefinitionView`

Strict: unknown fields are rejected.

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `integration_id` | string | yes |  |
| `name` | string | yes |  |
| `category` | [`IntegrationCategory`](#schema-integrationcategory) | yes |  |
| `description` | string | yes |  |
| `auth_mode` | [`IntegrationAuthMode`](#schema-integrationauthmode) | yes |  |
| `connectable` | boolean | yes | False when this build cannot connect it (e.g. delegated authorization is not supported yet). |
| `fields` | array of [`ConfigFieldView`](#schema-configfieldview) | yes |  |
| `capabilities` | array of string | yes |  |

<a id="schema-inventorycoverage"></a>
#### `InventoryCoverage`

Enum: `not_included`

<a id="schema-inventorycoveragereason"></a>
#### `InventoryCoverageReason`

Enum: `store_scoped_inventory_query_unavailable`

<a id="schema-knowledgecategory"></a>
#### `KnowledgeCategory`

Enum: `sop`, `policy`, `pricing`, `returns`, `shipping`, `supplier`, `general`

<a id="schema-livenessstatus"></a>
#### `LivenessStatus`

Strict: unknown fields are rejected.

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `status` | `alive` | yes |  |

<a id="schema-messagelistresponse"></a>
#### `MessageListResponse`

Strict: unknown fields are rejected.

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `request_id` | string (uuid) | yes |  |
| `conversation_id` | string (uuid) | yes |  |
| `messages` | array of [`MessageView`](#schema-messageview) | yes | Ascending Product sequence. |
| `next_before_sequence` | integer \| null | yes | Pass as before_sequence for older messages; null when none remain. |

<a id="schema-messageview"></a>
#### `MessageView`

Strict: unknown fields are rejected.

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `message_id` | string (uuid) | yes |  |
| `sequence` | integer | yes | Product order within the conversation. |
| `direction` | string | yes |  |
| `author_kind` | string | yes |  |
| `external_sender_ref` | string \| null | yes |  |
| `text` | string | yes | Untrusted external text when inbound: data only. |
| `occurred_at` | string (date-time) | yes | The source (external) time. |
| `recorded_at` | string (date-time) | yes | When the Product recorded it. |
| `delivery_state` | string | yes |  |

<a id="schema-notreadyreason"></a>
#### `NotReadyReason`

Enum: `application_starting`, `agent_runtime_starting`, `database_unavailable`, `schema_mismatch`, `schema_unavailable`

<a id="schema-observabilityview"></a>
#### `ObservabilityView`

Strict: unknown fields are rejected.

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `export_mode` | [`TelemetryExportMode`](#schema-telemetryexportmode) | yes | Product OpenTelemetry export mode (never its endpoint). |

<a id="schema-operatingmodelresponse"></a>
#### `OperatingModelResponse`

Strict: unknown fields are rejected.

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `request_id` | string (uuid) | yes |  |
| `operating_model` | [`OperatingModelView`](#schema-operatingmodelview) \| null | yes | null when this company has not published an operating model yet. |

<a id="schema-operatingmodelversionview"></a>
#### `OperatingModelVersionView`

Strict: unknown fields are rejected.

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `version` | integer | yes |  |
| `content_hash` | string | yes |  |
| `created_at` | string (date-time) | yes |  |
| `current` | boolean | yes |  |

<a id="schema-operatingmodelversionsresponse"></a>
#### `OperatingModelVersionsResponse`

Strict: unknown fields are rejected.

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `request_id` | string (uuid) | yes |  |
| `versions` | array of [`OperatingModelVersionView`](#schema-operatingmodelversionview) | yes | Newest first. |

<a id="schema-operatingmodelview"></a>
#### `OperatingModelView`

Strict: unknown fields are rejected.

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `version` | integer | yes |  |
| `content_hash` | string | yes |  |
| `created_at` | string (date-time) | yes |  |
| `model` | object | yes | The validated CompanyOperatingModel (structured, authoritative for its fields). |

<a id="schema-operationsrunrequest"></a>
#### `OperationsRunRequest`

Strict: unknown fields are rejected.

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `message` | string | yes | min length `1`, max length `8000` |
| `store_id` | string (uuid) | yes |  |

<a id="schema-operationsrunresponse"></a>
#### `OperationsRunResponse`

Strict: unknown fields are rejected.

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `request_id` | string (uuid) | yes |  |
| `message` | string | yes |  |

<a id="schema-operationsticketcommandstatusresponse"></a>
#### `OperationsTicketCommandStatusResponse`

Strict: unknown fields are rejected.

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `request_id` | string (uuid) | yes |  |
| `command_id` | string (uuid) | yes |  |
| `status` | [`TicketCommandStatus`](#schema-ticketcommandstatus) | yes |  |
| `reason` | [`TicketCommandReason`](#schema-ticketcommandreason) \| null | yes |  |
| `ticket_id` | string (uuid) \| null | yes |  |
| `created_at` | string (date-time) | yes |  |
| `updated_at` | string (date-time) | yes |  |

<a id="schema-operationsticketrequest"></a>
#### `OperationsTicketRequest`

Strict: unknown fields are rejected.

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `store_id` | string (uuid) | yes |  |
| `title` | string | yes | min length `1`, max length `160` |
| `description` | string | yes | min length `1`, max length `4000` |

<a id="schema-operationsticketresponse"></a>
#### `OperationsTicketResponse`

Strict: unknown fields are rejected.

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `request_id` | string (uuid) | yes |  |
| `command_id` | string (uuid) | yes |  |
| `status` | [`TicketCommandStatus`](#schema-ticketcommandstatus) | yes |  |
| `reason` | [`TicketCommandReason`](#schema-ticketcommandreason) \| null | yes |  |
| `ticket_id` | string (uuid) \| null | yes |  |
| `replayed` | boolean | yes |  |
| `persistence_complete` | boolean | yes |  |

<a id="schema-orderstatus"></a>
#### `OrderStatus`

Enum: `draft`, `pending`, `confirmed`, `processing`, `fulfilled`, `cancelled`, `completed`, `unknown`

<a id="schema-orderstatuscount"></a>
#### `OrderStatusCount`

Strict: unknown fields are rejected.

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `status` | [`OrderStatus`](#schema-orderstatus) | yes |  |
| `count` | integer | yes | min `0.0` |

<a id="schema-orderscoverage"></a>
#### `OrdersCoverage`

Enum: `created_in_business_day`

<a id="schema-overallstatus"></a>
#### `OverallStatus`

Enum: `ready`, `not_ready`

<a id="schema-productagentresponse"></a>
#### `ProductAgentResponse`

Strict: unknown fields are rejected.

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `request_id` | string (uuid) | yes |  |
| `agent` | [`AgentView`](#schema-agentview) | yes |  |

<a id="schema-proposalout"></a>
#### `ProposalOut`

Strict: unknown fields are rejected.

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `proposal_id` | string (uuid) | yes |  |
| `turn_id` | string (uuid) | yes |  |
| `action` | string | yes |  |
| `title` | string | yes |  |
| `description` | string | yes |  |
| `state` | [`ProposalState`](#schema-proposalstate) | yes |  |
| `command_id` | string (uuid) \| null | yes |  |
| `created_at` | string (date-time) | yes |  |
| `updated_at` | string (date-time) | yes |  |

<a id="schema-proposalrequest"></a>
#### `ProposalRequest`

Strict: unknown fields are rejected.

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `proposal_id` | string (uuid) | yes |  |

<a id="schema-proposalstate"></a>
#### `ProposalState`

Enum: `proposed`, `confirming`, `submitted`, `cancelled`

<a id="schema-publishoperatingmodelrequest"></a>
#### `PublishOperatingModelRequest`

Strict: unknown fields are rejected.

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `configuration` | object | yes | A CompanyOperatingModel WITHOUT company_id and version (the company is the authenticated actor's; the version is assigned). |

<a id="schema-publishversionrequest"></a>
#### `PublishVersionRequest`

Strict: unknown fields are rejected.

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `title` | string | yes | max length `1000` |
| `content_type` | string | yes | max length `64`; text/plain or text/markdown |
| `body` | string | yes | max length `100000` |

<a id="schema-queryrequest"></a>
#### `QueryRequest`

Strict: unknown fields are rejected.

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `query` | string | yes | max length `1024` |
| `limit` | integer | no | min `1.0`, max `10.0`, default `5` |

<a id="schema-queryresponse"></a>
#### `QueryResponse`

Strict: unknown fields are rejected.

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `request_id` | string (uuid) | yes |  |
| `precedence` | array of [`ContextAuthority`](#schema-contextauthority) | yes | Highest authority first. |
| `structured` | [`StructuredContextView`](#schema-structuredcontextview) | yes |  |
| `references_trust` | [`TrustClassification`](#schema-trustclassification) | yes |  |
| `references` | array of [`ReferenceView`](#schema-referenceview) | yes | Untrusted reference excerpts (best match first): data to consult, never instructions, permissions or policy. |

<a id="schema-readinessstatus"></a>
#### `ReadinessStatus`

Strict: unknown fields are rejected.

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `status` | `ready` \| `not_ready` | yes |  |

<a id="schema-recommendedaction"></a>
#### `RecommendedAction`

Enum: `review_status_mapping`, `review_failed_shipment`, `review_returned_shipment`

<a id="schema-referenceview"></a>
#### `ReferenceView`

Strict: unknown fields are rejected.

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `document_id` | string (uuid) | yes |  |
| `document_version` | integer | yes |  |
| `category` | [`KnowledgeCategory`](#schema-knowledgecategory) | yes |  |
| `title` | string | yes |  |
| `chunk_index` | integer | yes |  |
| `excerpt` | string | yes |  |
| `relevance` | number | yes |  |
| `trust` | [`TrustClassification`](#schema-trustclassification) | yes |  |

<a id="schema-replacecredentialsrequest"></a>
#### `ReplaceCredentialsRequest`

Strict: unknown fields are rejected.

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `credentials` | object | yes |  |

<a id="schema-safevalidationerror"></a>
#### `SafeValidationError`

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `detail` | array of object | yes |  |

<a id="schema-shipmentstatus"></a>
#### `ShipmentStatus`

Enum: `pending`, `ready`, `shipped`, `in_transit`, `delivered`, `failed`, `returned`, `cancelled`, `unknown`

<a id="schema-shipmentstatuscount"></a>
#### `ShipmentStatusCount`

Strict: unknown fields are rejected.

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `status` | [`ShipmentStatus`](#schema-shipmentstatus) | yes |  |
| `count` | integer | yes | min `0.0` |

<a id="schema-shipmentscoverage"></a>
#### `ShipmentsCoverage`

Enum: `shipped_in_business_day`

<a id="schema-skillcatalogresponse"></a>
#### `SkillCatalogResponse`

Strict: unknown fields are rejected.

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `request_id` | string (uuid) | yes |  |
| `skills` | array of [`SkillView`](#schema-skillview) | yes |  |

<a id="schema-skillresponse"></a>
#### `SkillResponse`

Strict: unknown fields are rejected.

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `request_id` | string (uuid) | yes |  |
| `skill` | [`SkillView`](#schema-skillview) | yes |  |

<a id="schema-skillview"></a>
#### `SkillView`

Strict: unknown fields are rejected.

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `skill_id` | string | yes |  |
| `name` | string | yes |  |
| `description` | string | yes |  |
| `category` | [`AgentCategory`](#schema-agentcategory) | yes |  |
| `lifecycle` | [`AgentLifecycle`](#schema-agentlifecycle) | yes |  |
| `capabilities` | array of string | yes |  |
| `tool_ids` | array of string | yes | Product tool IDs (their access and governed actions are in the owning Agent's manifest). |
| `requirements` | array of string | yes | Product domain requirements (never a provider). |
| `agent_ids` | array of string | yes | Product Agents that possess this Skill. |
| `task_ids` | array of string | yes | Product Tasks that require this Skill. |

<a id="schema-sourceview"></a>
#### `SourceView`

Strict: unknown fields are rejected.

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `kind` | string | yes | action \| write_command \| workflow_step |
| `command_id` | string (uuid) \| null | yes |  |
| `workflow_run_id` | string (uuid) \| null | yes |  |
| `workflow_id` | string \| null | yes |  |
| `workflow_step_id` | string \| null | yes |  |

<a id="schema-stepattemptstatus"></a>
#### `StepAttemptStatus`

Enum: `running`, `succeeded`, `failed`, `timed_out`, `requires_human`, `awaiting_approval`

<a id="schema-stepattemptview"></a>
#### `StepAttemptView`

Strict: unknown fields are rejected.

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `step_id` | string | yes |  |
| `attempt` | integer | yes |  |
| `handler_id` | string | yes |  |
| `status` | [`StepAttemptStatus`](#schema-stepattemptstatus) | yes |  |
| `failure_code` | [`WorkflowFailureCode`](#schema-workflowfailurecode) \| null | yes |  |
| `verification_code` | [`VerificationCode`](#schema-verificationcode) \| null | yes |  |
| `started_at` | string (date-time) | yes |  |
| `completed_at` | string (date-time) \| null | yes |  |

<a id="schema-stepsideeffect"></a>
#### `StepSideEffect`

Enum: `read_only`, `governed_write`

<a id="schema-structuredcontextview"></a>
#### `StructuredContextView`

Strict: unknown fields are rejected.

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `available` | boolean | yes |  |
| `authority` | [`ContextAuthority`](#schema-contextauthority) | yes |  |
| `operating_model` | [`OperatingModelView`](#schema-operatingmodelview) \| null | yes |  |

<a id="schema-summaryview"></a>
#### `SummaryView`

Strict: unknown fields are rejected.

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `title` | string | yes |  |
| `description` | string | yes |  |
| `changes` | array of [`ChangeView`](#schema-changeview) | yes |  |

<a id="schema-systemstatusresponse"></a>
#### `SystemStatusResponse`

Strict: unknown fields are rejected.

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `request_id` | string (uuid) | yes |  |
| `application` | [`ApplicationView`](#schema-applicationview) | yes |  |
| `overall` | [`OverallStatus`](#schema-overallstatus) | yes |  |
| `reasons` | array of [`NotReadyReason`](#schema-notreadyreason) | yes | Stable codes; empty when ready. |
| `components` | [`ComponentsView`](#schema-componentsview) | yes |  |
| `observability` | [`ObservabilityView`](#schema-observabilityview) | yes |  |

<a id="schema-taskcatalogresponse"></a>
#### `TaskCatalogResponse`

Strict: unknown fields are rejected.

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `request_id` | string (uuid) | yes |  |
| `tasks` | array of [`TaskView`](#schema-taskview) | yes |  |

<a id="schema-taskinputfieldview"></a>
#### `TaskInputFieldView`

Strict: unknown fields are rejected.

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `name` | string | yes |  |
| `label` | string | yes |  |
| `kind` | [`TaskInputKind`](#schema-taskinputkind) | yes |  |
| `required` | boolean | yes |  |
| `description` | string | yes |  |
| `max_length` | integer \| null | yes |  |

<a id="schema-taskinputkind"></a>
#### `TaskInputKind`

Enum: `text`, `uuid`, `date`, `boolean`, `integer`

<a id="schema-tasklimitsview"></a>
#### `TaskLimitsView`

Strict: unknown fields are rejected.

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `max_tool_calls` | integer | yes |  |
| `writes_possible` | boolean | yes |  |
| `allowed_write_actions` | array of string | yes |  |
| `requires_explicit_write_intent` | boolean | yes |  |

<a id="schema-taskresponse"></a>
#### `TaskResponse`

Strict: unknown fields are rejected.

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `request_id` | string (uuid) | yes |  |
| `task` | [`TaskView`](#schema-taskview) | yes |  |

<a id="schema-taskview"></a>
#### `TaskView`

Strict: unknown fields are rejected.

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `task_id` | string | yes |  |
| `name` | string | yes |  |
| `description` | string | yes |  |
| `category` | [`AgentCategory`](#schema-agentcategory) | yes |  |
| `lifecycle` | [`AgentLifecycle`](#schema-agentlifecycle) | yes |  |
| `skill_ids` | array of string | yes |  |
| `inputs` | array of [`TaskInputFieldView`](#schema-taskinputfieldview) | yes |  |
| `acceptance_criteria` | array of [`AcceptanceCriterionView`](#schema-acceptancecriterionview) | yes |  |
| `limits` | [`TaskLimitsView`](#schema-tasklimitsview) | yes |  |
| `agent_ids` | array of string | yes | Product Agents that support this Task. |
| `workflow_id` | string \| null | yes | The deterministic Product Workflow that performs this Task (None: it is not Workflow-backed). |

<a id="schema-telemetryexportmode"></a>
#### `TelemetryExportMode`

Enum: `disabled`, `otlp_http`

<a id="schema-threaddetailresponse"></a>
#### `ThreadDetailResponse`

Strict: unknown fields are rejected.

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `request_id` | string (uuid) | yes |  |
| `thread` | [`ThreadOut`](#schema-threadout) | yes |  |
| `turns` | array of [`TurnOut`](#schema-turnout) | yes |  |
| `proposals` | array of [`ProposalOut`](#schema-proposalout) | yes |  |

<a id="schema-threadout"></a>
#### `ThreadOut`

Strict: unknown fields are rejected.

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `thread_id` | string (uuid) | yes |  |
| `store_id` | string | yes |  |
| `agent_id` | string | yes |  |
| `created_at` | string (date-time) | yes |  |
| `updated_at` | string (date-time) | yes |  |

<a id="schema-threadresponse"></a>
#### `ThreadResponse`

Strict: unknown fields are rejected.

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `request_id` | string (uuid) | yes |  |
| `thread` | [`ThreadOut`](#schema-threadout) | yes |  |

<a id="schema-threadsresponse"></a>
#### `ThreadsResponse`

Strict: unknown fields are rejected.

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `request_id` | string (uuid) | yes |  |
| `threads` | array of [`ThreadOut`](#schema-threadout) | yes |  |

<a id="schema-ticketcommandreason"></a>
#### `TicketCommandReason`

Enum: `policy_denied`, `approval_required`, `audit_unavailable`, `handler_not_registered`, `input_invalid`, `handler_contract_violation`, `execution_failed_no_effect`, `execution_outcome_uncertain`, `verification_failed`, `verification_error`, `audit_incomplete`, `verified`, `command_execution_error`, `command_persistence_incomplete`

<a id="schema-ticketcommandstatus"></a>
#### `TicketCommandStatus`

Enum: `in_progress`, `denied`, `awaiting_approval`, `failed`, `requires_human`, `verified`

<a id="schema-ticketout"></a>
#### `TicketOut`

Strict: unknown fields are rejected.

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `command_id` | string (uuid) | yes |  |
| `status` | [`TicketCommandStatus`](#schema-ticketcommandstatus) | yes |  |
| `reason` | [`TicketCommandReason`](#schema-ticketcommandreason) \| null | yes |  |
| `ticket_id` | string (uuid) \| null | yes |  |
| `replayed` | boolean | yes |  |
| `persistence_complete` | boolean | yes |  |

<a id="schema-trustclassification"></a>
#### `TrustClassification`

Enum: `untrusted_reference`

<a id="schema-turnfailure"></a>
#### `TurnFailure`

Enum: `agent_disabled`, `run_failed`

<a id="schema-turnout"></a>
#### `TurnOut`

Strict: unknown fields are rejected.

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `turn_id` | string (uuid) | yes |  |
| `sequence` | integer | yes |  |
| `user_text` | string | yes |  |
| `assistant_text` | string \| null | yes |  |
| `status` | [`TurnStatus`](#schema-turnstatus) | yes |  |
| `failure` | [`TurnFailure`](#schema-turnfailure) \| null | yes |  |
| `created_at` | string (date-time) | yes |  |
| `completed_at` | string (date-time) \| null | yes |  |

<a id="schema-turnrequest"></a>
#### `TurnRequest`

Strict: unknown fields are rejected.

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `thread_id` | string (uuid) | yes |  |
| `turn_id` | string (uuid) | yes |  |
| `message` | string | yes | min length `1`, max length `8000` |

<a id="schema-turnresponse"></a>
#### `TurnResponse`

Strict: unknown fields are rejected.

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `request_id` | string (uuid) | yes |  |
| `replayed` | boolean | yes |  |
| `turn` | [`TurnOut`](#schema-turnout) | yes |  |
| `proposal` | [`ProposalOut`](#schema-proposalout) \| null | yes |  |

<a id="schema-turnstatus"></a>
#### `TurnStatus`

Enum: `pending`, `completed`, `failed`

<a id="schema-turnsresponse"></a>
#### `TurnsResponse`

Strict: unknown fields are rejected.

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `request_id` | string (uuid) | yes |  |
| `thread_id` | string (uuid) | yes |  |
| `turns` | array of [`TurnOut`](#schema-turnout) | yes |  |
| `proposals` | array of [`ProposalOut`](#schema-proposalout) | yes |  |

<a id="schema-updateconnectionrequest"></a>
#### `UpdateConnectionRequest`

Strict: unknown fields are rejected.

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `display_name` | string \| null | no |  |
| `config` | object \| null | no |  |

<a id="schema-verificationcode"></a>
#### `VerificationCode`

Enum: `verified`, `not_verified`

<a id="schema-workflowcatalogresponse"></a>
#### `WorkflowCatalogResponse`

Strict: unknown fields are rejected.

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `request_id` | string (uuid) | yes |  |
| `workflows` | array of [`WorkflowDefinitionView`](#schema-workflowdefinitionview) | yes |  |

<a id="schema-workflowcategory"></a>
#### `WorkflowCategory`

Enum: `operations`

<a id="schema-workflowdefinitionview"></a>
#### `WorkflowDefinitionView`

Strict: unknown fields are rejected.

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `workflow_id` | string | yes |  |
| `name` | string | yes |  |
| `description` | string | yes |  |
| `category` | [`WorkflowCategory`](#schema-workflowcategory) | yes |  |
| `version` | integer | yes |  |
| `lifecycle` | [`WorkflowLifecycle`](#schema-workflowlifecycle) | yes |  |
| `inputs` | array of [`WorkflowInputFieldView`](#schema-workflowinputfieldview) | yes |  |
| `steps` | array of [`WorkflowStepView`](#schema-workflowstepview) | yes | Executed strictly in this order. |

<a id="schema-workfloweventtype"></a>
#### `WorkflowEventType`

Enum: `workflow_requested`, `workflow_started`, `workflow_resumed`, `step_started`, `step_succeeded`, `step_failed`, `step_timed_out`, `step_retrying`, `workflow_succeeded`, `workflow_failed`, `workflow_requires_human`, `workflow_awaiting_approval`, `workflow_approval_resumed`

<a id="schema-workfloweventview"></a>
#### `WorkflowEventView`

Strict: unknown fields are rejected.

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `sequence` | integer | yes |  |
| `event_type` | [`WorkflowEventType`](#schema-workfloweventtype) | yes |  |
| `step_id` | string \| null | yes |  |
| `attempt` | integer \| null | yes |  |
| `status` | string \| null | yes |  |
| `failure_code` | [`WorkflowFailureCode`](#schema-workflowfailurecode) \| null | yes |  |
| `occurred_at` | string (date-time) | yes |  |

<a id="schema-workflowfailurecode"></a>
#### `WorkflowFailureCode`

Enum: `input_invalid`, `handler_not_registered`, `access_denied`, `step_timeout`, `step_execution_failed`, `step_verification_failed`, `step_outcome_uncertain`, `checkpoint_invalid`, `retry_exhausted`, `executor_lost`, `approval_required`, `workflow_unavailable`, `lease_conflict`

<a id="schema-workflowinputfieldview"></a>
#### `WorkflowInputFieldView`

Strict: unknown fields are rejected.

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `name` | string | yes |  |
| `label` | string | yes |  |
| `kind` | [`WorkflowInputKind`](#schema-workflowinputkind) | yes |  |
| `required` | boolean | yes |  |
| `description` | string | yes |  |

<a id="schema-workflowinputkind"></a>
#### `WorkflowInputKind`

Enum: `text`, `uuid`, `date`, `boolean`, `integer`

<a id="schema-workflowlifecycle"></a>
#### `WorkflowLifecycle`

Enum: `active`, `preview`, `deprecated`

<a id="schema-workflowresponse"></a>
#### `WorkflowResponse`

Strict: unknown fields are rejected.

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `request_id` | string (uuid) | yes |  |
| `workflow` | [`WorkflowDefinitionView`](#schema-workflowdefinitionview) | yes |  |

<a id="schema-workflowresumeresponse"></a>
#### `WorkflowResumeResponse`

Strict: unknown fields are rejected.

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `request_id` | string (uuid) | yes |  |
| `workflow_run_id` | string (uuid) | yes |  |
| `workflow_id` | string | yes |  |
| `status` | string | yes |  |
| `failure_code` | string \| null | yes |  |

<a id="schema-workflowrunlistresponse"></a>
#### `WorkflowRunListResponse`

Strict: unknown fields are rejected.

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `request_id` | string (uuid) | yes |  |
| `runs` | array of [`WorkflowRunView`](#schema-workflowrunview) | yes | This company's runs, newest first. |

<a id="schema-workflowrunresponse"></a>
#### `WorkflowRunResponse`

Strict: unknown fields are rejected.

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `request_id` | string (uuid) | yes |  |
| `run` | [`WorkflowRunView`](#schema-workflowrunview) | yes |  |
| `attempts` | array of [`StepAttemptView`](#schema-stepattemptview) | yes | Every Step attempt (a retry is a new attempt; none is overwritten). |
| `events` | array of [`WorkflowEventView`](#schema-workfloweventview) | yes | Append-only lifecycle events (safe metadata; not the action audit). |

<a id="schema-workflowrunstatus"></a>
#### `WorkflowRunStatus`

Enum: `pending`, `running`, `succeeded`, `failed`, `requires_human`, `awaiting_approval`

<a id="schema-workflowrunview"></a>
#### `WorkflowRunView`

Strict: unknown fields are rejected.

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `run_id` | string (uuid) | yes |  |
| `workflow_id` | string | yes |  |
| `workflow_version` | integer | yes |  |
| `request_id` | string (uuid) | yes |  |
| `status` | [`WorkflowRunStatus`](#schema-workflowrunstatus) | yes |  |
| `current_step_id` | string \| null | yes |  |
| `failure_code` | [`WorkflowFailureCode`](#schema-workflowfailurecode) \| null | yes |  |
| `attempt_count` | integer | yes |  |
| `created_at` | string (date-time) | yes |  |
| `updated_at` | string (date-time) | yes |  |
| `completed_at` | string (date-time) \| null | yes |  |

<a id="schema-workflowstepview"></a>
#### `WorkflowStepView`

Strict: unknown fields are rejected.

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `step_id` | string | yes |  |
| `name` | string | yes |  |
| `description` | string | yes |  |
| `handler_id` | string | yes | Stable Product handler id (bound to trusted code by the deployment; never an import path). |
| `side_effect` | [`StepSideEffect`](#schema-stepsideeffect) | yes |  |
| `timeout_seconds` | integer | yes |  |
| `max_attempts` | integer | yes | Bounded attempts; governed writes run once. |
| `checkpoint_policy` | [`CheckpointPolicy`](#schema-checkpointpolicy) | yes |  |

<!-- END GENERATED ENDPOINT REFERENCE -->
