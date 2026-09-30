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

## Security model

- **Same-origin proxy (BFF) only.** The browser calls five fixed routes and nothing else:
  `GET /api/product/health`, `POST /api/product/operations/runs`,
  `GET /api/product/operations/reports/daily`, `POST /api/product/operations/tickets`,
  `GET /api/product/operations/tickets/commands`. Each maps to exactly one Product API
  route (`lib/product-api/server.ts`). There is no catch-all route, no client-supplied
  path or origin, and **AgentOS routes (`/agents`, `/info`, `/sessions`, …) are never
  proxied**.
- **`PRODUCT_API_ORIGIN` is server-only** (never `NEXT_PUBLIC_*`) and must be a bare
  `http(s)://host[:port]` origin: credentials, a path, a query or a fragment make every
  proxy call fail closed.
- The proxy forwards only `Authorization` (not for health) and, for ticket creation,
  `Idempotency-Key`, plus its own `Content-Type`. Browser cookies, `Host`,
  `X-Forwarded-*`, `X-Request-ID`, `Referer`, `Origin` and custom headers are dropped.
  Only `store_id`/`business_date` (daily report) and `command_id` (command status) pass,
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

## Not yet

The Task 026 deployment package ships the **API only**: this console has no container
image and is not part of `deployments/template` yet (packaging and public ingress are a
later task). No login, store discovery, approvals, polling, charts or analytics.
