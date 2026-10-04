# Employee Chat: Ask Agento (Task 042)

**Ask Agento** lets an authenticated company employee talk to the existing **Operations
Agent** from any page of the Control Center. The conversation is multi-turn and stored by
the Product.

The Agent can analyse operations with its governed read tools. It can also **propose** an
operational ticket. A proposal never executes anything: a ticket is created only when the
employee explicitly confirms it. That confirmation runs through the existing governed
ticket WriteCommand path.

This is **not** Customer Chat. There is no customer authentication, CX Agent, provider
channel, outbound messaging, streaming or worker. The external Conversation transcript of
Task 037 (`product.conversations`, `/conversations`) is unchanged and separate.

## The rule

> Free-form model output never authorizes a business write.

- The Agent may only **propose**, with the chat-only tool
  `propose_operational_ticket(title, description)`. That tool has no side effect: no
  database write, coordinator, WriteCommand, integration or audit. It validates the typed
  title and description, checks that the actor may create tickets, and records at most
  **one** proposal per turn in a per-run, in-memory sink.
- The Product stores the proposal with the completed turn (`operations.ticket.create`
  only). There is no generic action, no JSON payload and no `/execute`.
- A write happens only after a **separate, explicit human confirmation request**. The
  client sends the `proposal_id` and exactly one `Idempotency-Key`, and never the title,
  description, action or store. The server then runs these steps in order:
  1. Reload the **stored** proposal.
  2. Check ownership.
  3. Check that the store is still granted.
  4. Check the fixed action.
  5. Call the existing `OperationsTicketCommandService`, which applies Permission and
     Policy, then the WriteCommand, Execution, Verification and Audit.
- The model is told to answer `Ticket prepared. Confirm the action to create it.` The
  runner appends that sentence whenever a proposal exists. The UI shows **Ticket created**
  only when the confirmation's ticket status is `verified`.

## Flow

```
Browser (Ask Agento drawer)
  -> same-origin BFF: fixed routes /api/product/chat/*  (no catch-all)
  -> Product API /api/v1/chat/*  (Product auth; AgentOS-exempt exact paths)
  -> EmployeeChatService                       (no Agno import)
       -> EmployeeChatRepository (PostgreSQL, migration 0009)
       -> OperationsChatRunService (Agent-gated) -> OperationsChatRunner -> Agno Agent
       -> OperationsTicketCommandService (confirmation only)
```

The chat route imports neither Agno nor an Agent. No new Agent is added to the catalog. The
chat runner is the **same** `operations` Agent identity, with the same model, read tools
and Agent gate, built with chat instructions and a chat-only tool set:

- `get_order`
- `get_order_shipments`
- `get_daily_operations_report`
- `propose_operational_ticket`

The write tool `create_operational_ticket` is **not** offered in chat.

## Storage (migration `0009`, on `0008`)

| Table | What it holds |
| --- | --- |
| `product.chat_threads` | One employee's thread: company + actor id + actor type + store; `agent_id` fixed to `operations`; the next turn sequence. |
| `product.chat_turns` | One idempotent turn (client `turn_id`): user text (1-8,000 characters), assistant text (at most 16,000), status `pending` / `completed` / `failed`, failure code. |
| `product.chat_action_proposals` | At most one typed proposal per turn: `operations.ticket.create` only, title (1-160), description (1-4,000). State `proposed` → `confirming` (one key hash) → `submitted` (`command_id`) or `cancelled`. |

CHECK constraints enforce the agent id, action name, lengths, states and their
consistency. The downgrade drops only these three tables.

## Identity and isolation

Company, actor and store come only from the authenticated `ActorContext`. The client
supplies a store selector for a new thread, which must be in `actor.store_ids` (403
otherwise; nothing is created).

- A thread belongs to **company + actor (id and type) + store**.
- Another actor's thread, another company's thread or an unknown thread all answer
  **404**, with the same answer each time.
- The thread's store must **still** be granted on every read, turn, confirmation and
  cancellation. Revoking a store hides its threads (404).
- Every repository query carries the trusted company (and, for threads, the actor) in SQL.

## API (fixed paths; Product auth only)

| Method | Path | Body / query |
| --- | --- | --- |
| GET | `/api/v1/chat/threads` | `?store_id=`: the actor's threads in that store (newest first, at most 50). |
| POST | `/api/v1/chat/threads` | `{store_id}`: a new, empty thread. **No model call.** |
| GET | `/api/v1/chat/thread` | `?thread_id=`: the thread, its turns (at most 200) and proposals. |
| GET | `/api/v1/chat/turns` | `?thread_id=`: the turns and proposals. |
| POST | `/api/v1/chat/turns` | `{thread_id, turn_id, message}`: strict, trimmed, 1-8,000 characters, extra fields refused. |
| POST | `/api/v1/chat/ticket-proposals/confirm` | `{proposal_id}` + exactly one `Idempotency-Key`. |
| POST | `/api/v1/chat/ticket-proposals/cancel` | `{proposal_id}`. |

Error answers are fixed and never echo submitted values:

| Status | Meaning |
| --- | --- |
| 403 | Forbidden. |
| 404 | `Chat thread not found` or `Ticket proposal not found`. |
| 409 | `Operations Agent is disabled`, `Chat turn conflict`, `Chat turn in progress`, `Ticket proposal was cancelled`, `Ticket proposal was already confirmed` or `Idempotency conflict`. |
| 400 | `Idempotency-Key required` or `Invalid Idempotency-Key`. |
| 422 | Validation failed; the safe answer does not echo the input. |
| 503 | `Employee chat unavailable`. |

### Turn idempotency

- A **completed replay** of the same `turn_id` and message returns the stored answer (200,
  `replayed: true`) **without calling the model**.
- The same id with a different message, or in another thread, is 409.
- A turn that is still pending is 409 `Chat turn in progress`.
- Concurrent submissions of one id create **one** turn and **one** model run (primary key
  plus a thread `FOR UPDATE` lock for the sequence).

### The Agent gate

A disabled `operations` Agent refuses a turn with **409 "Operations Agent is disabled"**
**before** any turn is recorded, model called, tool called or proposal created. The gate
is checked again right before the run. If the Agent is disabled between the two checks,
the turn is recorded as `failed` (`agent_disabled`), with no answer and no proposal.

### Confirmation and cancellation

- **Claim:** the confirmation is a compare-and-set from `proposed` to `confirming`. It
  binds the proposal to exactly **one** `Idempotency-Key`, stored only as its SHA-256.
- **Same key:** a retry with the same key reaches the ticket service, which **replays**
  the durable WriteCommand (200, `replayed: true`). It never creates a second command.
- **Another key:** once a key is bound, another key is refused with 409.
- **Unknown outcome:** if the ticket service outcome is unknown (an exception), the claim
  is kept so that a same-key retry completes it.
- **Idempotency conflict:** the key belonged to another request and nothing ran for this
  proposal. The claim is released and the call answers 409.
- **Link:** `command_id` links the proposal to the WriteCommand once it exists.
- **Cancel:** a cancellation is a compare-and-set from `proposed` only. It is durable and
  terminal. A confirmation after a cancellation is 409. A cancellation after a
  confirmation is 409.
- **Concurrency:** a confirmation racing a cancellation produces exactly one outcome.
- **HTTP status:** a confirmation answers like the ticket API: 201 for a fresh verified
  ticket, 200 for a replay or a processed denial or failure, 202 when the ticket is
  pending or needs a human. The body's `ticket.status` is the business outcome.

## Context limits (no Agno memory)

The Product owns the history. The runner receives at most:

- the last **12** completed turns of the **same** thread before the current one (pending
  and failed turns are never context), oldest first;
- at most **24,000** characters of user plus assistant text in total. Turns are taken
  newest first while they fit; older turns are dropped whole and never cut.

History is handed over as plain user and assistant messages. It is context only: it never
changes identity, company, store, permissions or policy, and current facts still come from
the tools. These Agno features are disabled:

- memory;
- session storage;
- history reading;
- authorization by `session_id` or `user_id`.

## Web UI

The **Ask Agento** button appears in the shell (sidebar and mobile bar) on every page. It
opens a right-side drawer on desktop and a full-screen dialog on mobile. Escape closes it.
The drawer shows:

- the Agent identity;
- the store context (the session Store UUID);
- **New chat** and recent chats;
- the transcript;
- the composer;
- proposal cards.

Safety rules:

- **Plain text:** messages are plain text with newlines preserved. There is no Markdown,
  no HTML and no `dangerouslySetInnerHTML`.
- **Composer:** at most 8,000 characters. Enter sends, Shift+Enter inserts a newline
  (IME-safe). Send is disabled while a turn is in flight. Nothing is ever sent
  automatically.
- **No model call** happens on app load, drawer open, navigation, store entry, thread
  listing or thread selection.
- **Retries:** a retry of the same failed message in the same thread reuses its `turn_id`.
- **Confirmation keys:** a confirmation generates one `Idempotency-Key` per proposal and
  reuses it for that proposal's retries.
- **Session safety:** chat state is keyed by the operations epoch. Changing the Store,
  replacing the key or disconnecting clears the selected thread, the transcript and every
  pending result.
- **Storage:** the API key stays **memory only**. Chat adds no localStorage,
  sessionStorage, IndexedDB, cookie, URL or log use, and a hard reload still forgets the
  key.
- **BFF routes:** the BFF has five explicit routes with no catch-all. They forward only
  `Authorization`, plus `Idempotency-Key` for the confirmation only. A chat message request
  is capped at 64 KiB.
- **Error details:** the browser shows only Product error details from a fixed
  allowlist.
- **No streaming:** there is no WebSocket, SSE or polling. Checking a submitted ticket's
  status is an explicit button.

## Observability

Chat observations are bounded, service-level events:

| Operation | Events |
| --- | --- |
| `chat.thread` | create, list, read |
| `chat.turn` | completed, replayed, failed, refused |
| `chat.ticket_proposal` | proposed, confirmed, cancelled, refused |

They carry outcomes and enums only, never message text, answers, titles, descriptions,
keys or ids. The ticket confirmation is also observed and audited by the existing ticket
WriteCommand path.

## Local demo

```bash
./scripts/demo.sh reset && ./scripts/demo.sh up
```

1. Open http://127.0.0.1:3000, connect with the printed Product API key, and set the
   printed Store UUID (Operations page, or the drawer's Store field).
2. Click **Ask Agento** and send `Analyze operations for 2026-03-03.` The deterministic
   demo model calls the daily report tool and summarises it in the chat.
3. Send `Create an operational ticket titled "Investigate failed shipment" with
   description "Review the failed shipment found in the demo operations report."` The
   demo model calls only `propose_operational_ticket`, and the chat shows a proposal card.
4. Click **Confirm and create ticket**. The real ticket WriteCommand path runs and the
   card shows **Ticket created** with the verified ticket id.

The demo model is LOCAL/TEST-only and refused in staging and production.

## Limitations

- **Single Agent:** only the Operations Agent and only the `operations.ticket.create`
  proposal. There is no other chat action.
- **No streaming:** answers arrive in one response, a turn can take as long as the model
  run, and there is no cancel-in-flight.
- **No rename, search, deletion or retention policy** for threads. Listing returns the 50
  most recent threads of the store, and a thread view returns its 200 most recent turns.
- **No summarization:** context is the bounded recent history above, so older turns fall
  out of context.
- **Lost keys:** if a confirmation's outcome was unknown and the browser lost its
  Idempotency-Key (for example after a reload), the proposal stays `confirming`. Only that
  key can complete it, and a confirmation with another key is refused (409). No second
  WriteCommand is ever created.
- **Stuck turns:** a turn whose run crashed before it was marked failed stays `pending`. A
  replay answers 409 "in progress"; send a new message instead.
- **Model compliance:** the model is instructed, not trusted, to say "Ticket prepared".
  The UI and the API decide what is shown as created.
- **No Customer/CX Chat:** no external channel and no customer identity.
