# Agento Product Control Center

Task 038 turns the separate web pages into one product: **Agento — AI Operating Layer**.
It adds one application shell, one in-memory Product session and an Overview dashboard.
It is a frontend-only change. It adds no backend endpoint, no aggregation API, no
migration (head stays `0008`) and no dependency.

## Routes

| Route | What it is |
| --- | --- |
| `/` | **Overview**: the Control Center dashboard (no longer the Operations Console). |
| `/operations` | The existing Operations Console: analysis, daily report, ticket, command status. `?tab=analysis\|report\|ticket\|command` selects a section; any other value falls back to analysis. Selecting a tab never runs anything. |
| `/approvals` | Approvals (canonical). |
| `/conversations` | Conversations (read-only). |
| `/workflows` | Workflows (canonical, read-only). |
| `/settings/agents` | Agents. |
| `/settings/integrations` | Integrations. |
| `/settings/knowledge` | Knowledge. |
| `/system` | System (Task 039): the read-only operational status of this installation (`system.read`). See [`PRODUCTION_OPERATIONS.md`](PRODUCTION_OPERATIONS.md). |
| `/settings/approvals` | Redirects to `/approvals`. |
| `/settings/workflows` | Redirects to `/workflows`. |

Each area has exactly one implementation. The legacy routes only redirect, and the
canonical pages never redirect back.

## Shell

`apps/web/components/shell/` owns everything that is not page content:

- `AppShell`: branding ("Agento", "AI Operating Layer"), the left sidebar on desktop, a
  top bar with an accessible **Menu** toggle (`aria-expanded`, `aria-controls`, closes on
  navigation and Escape) on narrow screens, a skip link and the `<main>` landmark.
- `ProductNavigation`: **Work** (Overview, Operations, Approvals, Conversations,
  Workflows) and **Configure** (Agents, Integrations, Knowledge, System), with `aria-current="page"`
  on the active route. Internal framework or runtime names never appear in navigation.
- `SessionControl`: API reachability, key status, connect / replace key and Disconnect.
- `PageHeader`: each page's single `<h1>`. The shell renders no `<h1>`.
- `ProductSessionProvider`: the one session (below).

Pages render only their own content. They have no header, navigation or key form of their
own. Shared presentational primitives live in `components/ui/primitives.tsx`.

## The Product session

There is **one** `ProductSessionProvider`, mounted once in the root layout, so the session
survives client-side navigation between pages.

- **The Product API key is memory only.** It lives in the provider's React state and is
  never written to localStorage, sessionStorage, IndexedDB, cookies, the URL or query,
  server props, HTML attributes, logs, analytics or error messages. It never reaches a
  Server Component. **A hard reload forgets it.**
- State: `apiKey`, `keyStatus` (`unset` / `unverified` / `accepted` / `rejected`),
  `sessionEpoch`, `operationsEpoch`, `storeId` and `recentCommandId`. The pure reducer is
  `components/shell/session.ts` and does not hold the key.
- **Epochs.** A new key or a disconnect advances both epochs. A Store change advances only
  the operations epoch. Authenticated pages are keyed by the session epoch. Operations
  results are keyed by the operations epoch, so a Store change clears Operations results
  without touching other pages. Completions from an older epoch are ignored.
- **Auth outcomes.** A 401 marks the key *rejected* everywhere. A 403 means authenticated
  but not permitted: the page or card says so, and the key is **not** marked rejected. Any
  success marks the key *accepted*. The provider's in-memory observer
  (`observeAuthOutcomes` in `lib/product-api/client.ts`, `components/shell/authObservation.ts`)
  works in two phases, so no call site changes:
  - **begin**, synchronously **before** any network I/O: the client shows the observer
    the key in this request's Authorization header. If it is the session's current key,
    the observer returns the **current session epoch** as an opaque, non-secret token.
    Otherwise it returns nothing and the request is not observed. The client keeps no key.
  - **complete**, after the exchange: the outcome (HTTP status only) is dispatched for the
    **captured** epoch, never the epoch current at completion time.

  The reducer drops any completion of an older epoch. So a request that began before a
  key replacement, a disconnect or a reconnect with the **same** key can never accept or
  reject the newer session. That session stays "not yet verified" until one of its own
  requests completes.
- **Health is separate from auth.** "API online / Checking / API unavailable" reflects
  `GET /api/product/health` only. It says nothing about the key.
- **Store.** The Store UUID is chosen on Operations and survives client navigation. It is
  request context, never authorization: the Product API decides access.
- **Disconnect** clears the key, the store and the recent command, and remounts every
  authenticated view, so no data from the old session stays on screen.

## Overview

Without a key, the Overview shows the product, an explanation, API reachability, a
**Connect to Product API** control (password input whose draft is cleared on submit) and
the product areas.

With a key, it issues **independent, parallel reads** over the existing Product API only:

| Card | Read |
| --- | --- |
| Operations Agent | `listAgents` → Available / Disabled / Unavailable, with links to Operations and Agents. |
| Approvals | `listApprovals(apiKey, "requested")` → the returned pending requests (title, risk, expiry). No invented totals. |
| Workflows | `listWorkflowRuns` → the most recent runs with their explicit statuses. |
| Conversations | `listConversations` → recent conversations (channel label, last activity). No message text. |
| Integrations | `getIntegrationCatalog` + `listIntegrationConnections` → "No integrations are installed in this build." when the catalog is empty. |
| Knowledge | `listKnowledgeDocuments` → readiness, active titles and categories only. No document text. |

Each card handles its own failure. A 403 shows "You don't have access to X." and a 503
shows "X is unavailable right now.". One card failing never blanks another.

**Needs attention** lists current signals derived only from statuses the Product
reported: API unavailable, the Operations Agent not available, approvals awaiting a
decision, recent Workflow runs that failed or timed out, recent runs that need a person,
and enabled connections whose last test failed. When some reads failed, it says so instead
of claiming everything is fine.

**Quick actions** (Analyze operations, Daily report, Create ticket) only navigate to
`/operations?tab=…`. Nothing runs until the user submits there.

**Refresh overview** re-issues the same reads explicitly. There is no polling.

The Overview **never** runs the Operations Agent or any model, loads the daily report,
creates a ticket, decides an approval, tests an integration or changes an Agent or any
other state. It shows no audit log.

## UI rules

- No new dependency (no Tailwind, component, icon or chart library) and no remote asset.
  System fonts only.
- Responsive: a left sidebar on desktop, and a collapsible accessible menu on tablet and
  mobile, with no horizontal overflow at 390 px.
- One `<h1>` per page, a visible focus outline, keyboard access (skip link, tabs with
  arrow keys) and statuses that are always written as text, never colour alone.
- Every area has an empty state.
- Untrusted text (approval titles, conversation channels, document titles) is rendered as
  inert text. There is no `dangerouslySetInnerHTML`.
- BFF security is unchanged: fixed routes only, no generic proxy, no upstream URL or
  Authorization value exposed or logged.
- Mutation pages keep their busy guards and confirmations.

## Tests

- `tests/web/test_control_center_architecture.py`: static guards. They check no `0009`,
  pinned dependency manifests, unchanged catalogs, no new BFF route, one provider and one
  shell, the key held in the provider only with no storage API, read-only Overview calls,
  quick actions that only navigate, Operations never running on its own, the redirects
  and no `innerHTML`.
- `tests/web/test_console_architecture.py`: the existing Operations and page guards,
  updated for the shell.
- `apps/web/scripts/session-epoch.test.mjs` (`npm run test:session`): the session reducer,
  including the 401 and 403 rules, and the two-phase observer (stale 401 and stale success
  after a same-key reconnect, a stale old-key request after a key switch, and a current
  request's accept/reject/403).
- `apps/web/scripts/product-proxy-smoke.mjs` (`npm run smoke:proxy`): every page renders
  inside the shell with one `<h1>` and no key, origin or framework name. The legacy routes
  redirect.
- `apps/web/scripts/control-center.browser.mjs`: Playwright browser tests against the
  built app and a **test-only** stub upstream (`control-center-stub.mjs`, 127.0.0.1 only).
  They cover:
  - the session surviving navigation;
  - a reload forgetting the key;
  - disconnect clearing data;
  - 401 rejection;
  - same-key reconnect, where a delayed old-session response (401, or success) never
    changes the new session;
  - 403/503 partial cards that keep the key accepted;
  - Overview read-only calls with no model call and no polling;
  - Operations regression;
  - deep links and redirects;
  - empty states, inert text and keyboard access;
  - no horizontal overflow at 390 px and 768 px.

  There are no pixel-golden screenshots. Playwright is not a dependency of the package,
  so the script uses a Playwright install already on the machine:

  ```bash
  cd apps/web && npm run build
  NODE_PATH="$(npm root -g)" node scripts/control-center.browser.mjs
  # optional: SCREENSHOT_DIR=/some/dir to keep review screenshots
  ```

## System (Task 039)

`/system` sits under Configure. It reads the Product-authenticated System Status once, and
again only when Refresh is pressed; there is no polling. It uses the same in-memory
session: 401 rejects the key and 403 does not. It shows fixed component states and
stable reasons only, never a URL, host, endpoint, identifier or secret. The Overview's
read set is unchanged: it only links to System. The Web container health check uses the
separate public, minimal BFF route `/api/product/health/ready`, which needs no Product
API key.

## Ask Agento (Task 042)

The shell has a global **Ask Agento** button, in the sidebar and in the mobile top bar, on
every page. It opens `components/chat/ChatDrawer.tsx`: a right-side drawer on desktop and
a full-screen dialog below 860 px. Escape closes it.

The drawer contains the Operations Agent identity, the store context, **New chat**,
recent chats, the transcript, a composer and ticket proposal cards. It uses the same
in-memory session and its Store UUID. Chat state is keyed by the **operations epoch**, so
a Store change, a key replacement or a disconnect clears the selected thread and the
transcript.

Rules:

- **No model call** happens on load, open, navigation, listing or thread selection: only
  sending a message runs the Agent.
- **Plain text:** answers are plain text (`white-space: pre-wrap`), never Markdown or HTML.
- **Composer:** at most 8,000 characters. Enter sends and Shift+Enter adds a line. Send is
  disabled while a turn is in flight.
- **Proposals:** a proposal card shows the title and description as text, with
  **Confirm and create ticket** and **Cancel**. The confirmation sends only the proposal
  id and one `Idempotency-Key`, reused for that proposal's retries. **Ticket created** is
  shown only for a `verified` ticket status.
- **BFF routes:** five fixed BFF routes under `/api/product/chat/`. Only the confirmation
  forwards an `Idempotency-Key`, and a chat message request is capped at 64 KiB.
- **No streaming:** no WebSocket, SSE or polling, and no storage of any kind.

The browser suite covers Ask Agento on every page, no request without a key, reads only
on open and selection, Enter / Shift+Enter, plain-text answers, the in-flight guard, the
explicit confirmation contract, cancellation, the disabled-Agent message, store change
and disconnect clearing, and the mobile full-screen dialog. Static guards are in
`tests/web/test_chat_drawer_architecture.py`. See [`EMPLOYEE_CHAT.md`](EMPLOYEE_CHAT.md).
