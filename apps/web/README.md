# apps/web — Operations Console

A minimal Operations Console over the existing Product API (Next.js App Router,
TypeScript, plain CSS; no UI framework, no analytics, no remote fonts). It is not an
AgentOS UI, a chatbot, an admin panel or a provider dashboard, and the backend stays
authoritative for authentication, store access, permissions, report rules and ticket
command semantics: the console only presents what the Product API returns.

```bash
npm ci
cp .env.example .env.local     # PRODUCT_API_ORIGIN=http://127.0.0.1:8000
npm run dev                    # http://localhost:3000
npm run typecheck && npm run build && npm run smoke:proxy
```

## What it does

| Section | Product API route | Notes |
|---|---|---|
| Session | `GET /health` | Product API key (password field, memory only), Store UUID, API reachability. Reachable does **not** mean the key is accepted; the key shows as accepted only after a Product request succeeds. |
| Analyze operations | `POST /api/v1/operations/runs` | **Read-only** Operations Agent analysis (message up to 8000 characters). Shows the answer and its `request_id`. Never writes. |
| Daily report | `GET /api/v1/operations/reports/daily` | The deterministic backend report as returned: business date, timezone, generated time, metrics, every status count (zeros included), findings in API order, `findings_total`/truncation and coverage (inventory is shown as **not analyzed**). A blank date is omitted so the Product decides the store’s business day. |
| Operational ticket | `POST /api/v1/operations/tickets` | Explicit write (title ≤ 160, description ≤ 4000), never through the Agent. |
| Command status | `GET /api/v1/operations/tickets/commands` | Manual lookup (auto-filled from the latest ticket), no polling. A 404 is only “Ticket command not found”. |
| Settings → Agents (`/settings/agents`) | `/api/v1/agents/*` | Product Agent lifecycle management: installed Product Agents (only the Operations Agent), effective availability, read-only manifest summary, Enable / Disable / Reset to default, plus each Agent's read-only Skills (tool bindings, read/write) and Tasks (required Skills, declared envelope, acceptance criteria) from `/api/v1/skills/*` and `/api/v1/tasks/*`. No prompt, model, tool, Skill, Task or code editor and no Agent creation. Product API only, never AgentOS. See [`docs/AGENTS.md`](../../docs/AGENTS.md). |
| Settings → Integrations (`/settings/integrations`) | `/api/v1/integrations/*` | Generic Connections UI: installed integration types grouped by category (this build installs none, and says so), connections with enabled state and last-known test result, a generic form (text/URL/boolean/write-only secret fields), Test, Enable/Disable, Edit settings, Replace credentials, Delete. Secret inputs are never pre-filled. See [`docs/INTEGRATIONS.md`](../../docs/INTEGRATIONS.md). |

## Security model

- **Same-origin proxy (BFF) only.** The browser calls fixed routes and nothing else:
  `GET /api/product/health`, `POST /api/product/operations/runs`,
  `GET /api/product/operations/reports/daily`, `POST /api/product/operations/tickets`,
  `GET /api/product/operations/tickets/commands`, and the integration-management routes
  under `/api/product/integrations/` (`catalog`, `connections`, `connection`,
  `connection/credentials`, `connection/test`, `connection/enable`,
  `connection/disable`), and the Agent-management routes under
  `/api/product/agent-management/` (`catalog`, `agents`, `agent`, `agent/enable`,
  `agent/disable`, `agent/configuration`, and the read-only `skills`, `skill`, `tasks`,
  `task`; deliberately not `/api/product/agents`, which
  stays unproxied like every AgentOS-like path). Each exported method maps to exactly one
  Product API method and path (`lib/product-api/server.ts`). There is no catch-all route, no client-supplied
  path or origin, and **AgentOS routes (`/agents`, `/info`, `/sessions`, …) are never
  proxied**.
- **`PRODUCT_API_ORIGIN` is server-only** (never `NEXT_PUBLIC_*`) and must be a bare
  `http(s)://host[:port]` origin: credentials, a path, a query or a fragment make every
  proxy call fail closed.
- The proxy forwards only `Authorization` (not for health) and, for ticket creation,
  `Idempotency-Key`, plus its own `Content-Type`. Browser cookies, `Host`,
  `X-Forwarded-*`, `X-Request-ID`, `Referer`, `Origin` and custom headers are dropped.
  Only `store_id`/`business_date` (daily report), `command_id` (command status),
  `connection_id` (single-connection integration routes), `agent_id` (single-Agent
  routes), `skill_id` and `task_id` (single Skill/Task routes) pass,
  each at most once; any other or repeated query parameter is rejected with 422. Request bodies are capped at 16 KiB (413),
  responses at 1 MiB; redirects are never followed; only `Content-Type`,
  `Cache-Control: no-store` and the Product `X-Request-ID` are returned. Timeouts,
  network errors, redirects, non-JSON or oversized answers all become
  `502 {"detail": "Product API unavailable"}` with no details. Nothing is cached, stored
  or logged.
- **The Product API key lives only in React memory** for the page lifetime. The
  application never writes it to localStorage, sessionStorage, IndexedDB, cookies, the
  URL or logs, never displays it, and forgets it on reload or on “Disconnect and clear
  session” (which also clears every result and form).
- **Store UUID is entered explicitly**: there is no store-discovery endpoint yet, so the
  console shows no store, company or actor names. The UUID format check is for UX only.
- **Ticket writes are idempotent and never retried automatically.** One UUID
  idempotency key is generated per ticket intent and sent only as the `Idempotency-Key`
  header (never shown, stored or put in a URL or body). One click sends at most one
  request and the button is disabled while it is in flight. After an ambiguous failure,
  “Retry same ticket request” re-sends the unchanged request with the same key so the
  Product replays instead of duplicating; changing the store, title or description, or
  “Reset ticket form”, discards the key. Only status `verified` is shown as a created
  ticket, whatever the HTTP status.

`npm run smoke:proxy` (run in CI after the build) starts a local stub Product API and the
built app on `127.0.0.1` and proves the route allowlist, header and query filtering,
body/response caps, redirect and failure handling, and that no key appears in the
rendered HTML, client bundles or server logs. It makes no external network call.

## Packaged deployment

`apps/web/Dockerfile` builds the Web image (build context = repository root):

```bash
docker build -f apps/web/Dockerfile -t commerce-ai-platform-web:0.1.0 .
```

Multi-stage on a digest-pinned Node 22 slim base, `npm ci` from `package-lock.json`,
Next's `output: "standalone"`: the image holds only `server.js`, the compiled server,
the traced runtime modules and the static assets, runs as UID/GID 10002 with `node
server.js` as PID 1, and health-checks through the BFF (`/api/product/health`). Next
telemetry is disabled at build and run time.

In `deployments/template` the `web` service is the **only host-published** service
(`127.0.0.1:${PRODUCT_WEB_PORT:-3000}`); the template fixes `PRODUCT_API_ORIGIN` to the
private `http://api:8000` (it is not an operator setting, and the browser never sees it).
The API and AgentOS are not host-published at all. `.env.example` in this directory is
for **source-tree** development only (`PRODUCT_API_ORIGIN=http://127.0.0.1:8000`); the
deployment does not use it. Remote/public ingress still needs a later, reviewed TLS /
reverse-proxy design.

## Not yet

No login, store discovery, approvals, polling, charts or analytics.
