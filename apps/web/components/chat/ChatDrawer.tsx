"use client";

// Ask Agento (Task 042): the employee's chat with the Operations Agent, available from
// every page as a right-side drawer (a full-screen dialog on small screens).
//
// Safety rules of this component:
//   - Nothing calls the model unless the employee SENDS a message: opening the drawer,
//     navigating, listing or selecting a thread only reads the Product API.
//   - Messages and answers are PLAIN TEXT (newlines kept): never Markdown or HTML.
//   - The Product API key stays in the ONE session provider (memory only). Chat state
//     lives in React state keyed by the operations epoch: a new key, a disconnect or a
//     Store change discards the threads, the transcript and every pending result.
//   - A ticket is only PROPOSED by the Agent. "Ticket created" is shown only when the
//     explicit confirmation's ticket status is "verified". A confirmation sends only the
//     proposal id and one Idempotency-Key, reused for a retry of the SAME proposal.
//   - No WebSocket, SSE or polling.
import { useCallback, useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import {
  cancelTicketProposal,
  confirmTicketProposal,
  createChatThread,
  getChatThread,
  getTicketCommand,
  listChatThreads,
  looksLikeUuid,
  newIdempotencyKey,
  sendChatTurn,
} from "../../lib/product-api/client";
import type {
  ChatProposalView,
  ChatThreadView,
  ChatTicketView,
  ChatTurnView,
  ProductResult,
} from "../../lib/product-api/types";
import { useProductSession } from "../shell/ProductSessionProvider";
import { Badge, Mono, type Tone } from "../ui/primitives";

export const MAX_CHAT_MESSAGE_CHARS = 8000;

export function ChatDrawer({ open, onClose }: { open: boolean; onClose: () => void }) {
  const session = useProductSession();
  const panelRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onKey = (event: globalThis.KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    panelRef.current?.focus();
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;
  const ready = session.apiKey !== null && looksLikeUuid(session.storeId);

  return (
    <div className="chat-layer">
      <button type="button" className="chat-backdrop" aria-label="Close Ask Agento" onClick={onClose} tabIndex={-1} />
      <div
        ref={panelRef}
        className="chat-drawer"
        role="dialog"
        aria-modal="true"
        aria-labelledby="chat-drawer-title"
        tabIndex={-1}
      >
        <header className="chat-drawer__header">
          <div>
            <h2 id="chat-drawer-title" className="chat-drawer__title">Ask Agento</h2>
            <p className="chat-drawer__identity">Operations Agent</p>
          </div>
          <button type="button" className="button button--ghost button--small" onClick={onClose}>
            Close
          </button>
        </header>
        <StoreContext />
        {session.apiKey === null ? (
          <div className="notice notice--neutral" role="status">
            Connect to the Product API first: use <strong>Session</strong> in the navigation panel.
          </div>
        ) : !ready ? (
          <div className="notice notice--neutral" role="status">Set the Store UUID to start chatting.</div>
        ) : (
          <ChatSession
            key={session.operationsEpoch}
            apiKey={session.apiKey}
            storeId={session.storeId}
            epoch={session.operationsEpoch}
            onAuthResult={(result) => session.reportResult(session.sessionEpoch, result)}
          />
        )}
      </div>
    </div>
  );
}

function StoreContext() {
  const { storeId, changeStore, apiKey } = useProductSession();
  return (
    <div className="chat-drawer__store">
      <label className="field__label" htmlFor="chat-store-id">Store UUID</label>
      <input
        id="chat-store-id"
        className="input mono"
        value={storeId}
        placeholder="Store UUID"
        disabled={apiKey === null}
        spellCheck={false}
        autoComplete="off"
        onChange={(event) => changeStore(event.target.value.trim())}
      />
      <span className="field__hint">Changing the store starts a fresh chat context.</span>
    </div>
  );
}

type Pending = { threadId: string; turnId: string; message: string };

function describe(result: ProductResult<unknown>): string {
  if (result.ok) return "";
  if (result.detail) return `${result.detail}.`;
  switch (result.error) {
    case "unauthenticated":
      return "The Product API key was not accepted.";
    case "forbidden":
      return "This key may not use this store.";
    case "not_found":
      return "Not found.";
    case "conflict":
      return "The request conflicts with the current state.";
    case "invalid":
      return "The request was not accepted.";
    case "service_unavailable":
      return "Employee chat is unavailable.";
    default:
      return "The Product API is unavailable. You can retry.";
  }
}

function ChatSession({ apiKey, storeId, epoch, onAuthResult }: {
  apiKey: string;
  storeId: string;
  epoch: number;
  onAuthResult: (result: ProductResult<unknown>) => void;
}) {
  const [threads, setThreads] = useState<ChatThreadView[] | null>(null);
  const [threadId, setThreadId] = useState<string | null>(null);
  const [turns, setTurns] = useState<ChatTurnView[]>([]);
  const [proposals, setProposals] = useState<ChatProposalView[]>([]);
  const [draft, setDraft] = useState("");
  const [pending, setPending] = useState<Pending | null>(null);
  const [failed, setFailed] = useState<Pending | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loadingThread, setLoadingThread] = useState(false);
  const busy = useRef(false);
  const transcriptRef = useRef<HTMLOListElement>(null);

  const track = useCallback(<T,>(result: ProductResult<T>): ProductResult<T> => {
    onAuthResult(result);
    return result;
  }, [onAuthResult]);

  // Reads only: never a model call.
  useEffect(() => {
    let live = true;
    void listChatThreads(apiKey, storeId).then((result) => {
      if (!live) return;
      track(result);
      if (result.ok) setThreads(result.data.threads);
      else setError(describe(result));
    });
    return () => {
      live = false;
    };
  }, [apiKey, storeId, track]);

  useEffect(() => {
    transcriptRef.current?.lastElementChild?.scrollIntoView?.({ block: "end" });
  }, [turns, pending]);

  const openThread = async (id: string) => {
    if (busy.current) return;
    setLoadingThread(true);
    setError(null);
    setFailed(null);
    const result = track(await getChatThread(apiKey, id));
    setLoadingThread(false);
    if (!result.ok) {
      setError(describe(result));
      return;
    }
    setThreadId(id);
    setTurns(result.data.turns);
    setProposals(result.data.proposals);
  };

  const newChat = () => {
    if (busy.current) return;
    setThreadId(null);
    setTurns([]);
    setProposals([]);
    setFailed(null);
    setError(null);
  };

  const submit = async (message: string) => {
    if (busy.current) return;
    const text = message.trim();
    if (text.length === 0 || text.length > MAX_CHAT_MESSAGE_CHARS) return;
    busy.current = true;
    setError(null);
    let current = threadId;
    if (current === null) {
      const created = track(await createChatThread(apiKey, storeId));
      if (!created.ok) {
        busy.current = false;
        setError(describe(created));
        return;
      }
      current = created.data.thread.thread_id;
      setThreadId(current);
      setThreads((list) => [created.data.thread, ...(list ?? [])]);
    }
    // A retry of the SAME message in the same thread reuses its turn id (idempotent).
    const retry = failed !== null && failed.threadId === current && failed.message === text;
    const attempt: Pending = { threadId: current, turnId: retry ? failed.turnId : newIdempotencyKey(), message: text };
    setPending(attempt);
    setFailed(null);
    setDraft("");
    const result = track(await sendChatTurn(apiKey, attempt));
    busy.current = false;
    setPending(null);
    if (!result.ok) {
      setError(describe(result));
      setFailed(attempt);
      setDraft(text);
      return;
    }
    const { turn, proposal } = result.data;
    setTurns((list) => [...list.filter((t) => t.turn_id !== turn.turn_id), turn]);
    if (proposal) setProposals((list) => [...list.filter((p) => p.proposal_id !== proposal.proposal_id), proposal]);
  };

  const onSubmit = (event: FormEvent) => {
    event.preventDefault();
    void submit(draft);
  };

  const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    // Enter sends; Shift+Enter inserts a newline; IME composition is never interrupted.
    if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault();
      void submit(draft);
    }
  };

  const updateProposal = (proposal: ChatProposalView) =>
    setProposals((list) => list.map((p) => (p.proposal_id === proposal.proposal_id ? proposal : p)));

  const sending = pending !== null;
  return (
    <div className="chat-session" data-epoch={epoch}>
      <section className="chat-threads" aria-label="Recent chats">
        <div className="chat-threads__header">
          <h3 className="chat-section-title">Recent chats</h3>
          <button type="button" className="button button--secondary button--small" onClick={newChat} disabled={sending}>
            New chat
          </button>
        </div>
        {threads === null ? (
          <p className="chat-muted">Loading chats…</p>
        ) : threads.length === 0 ? (
          <p className="chat-muted">No chats in this store yet.</p>
        ) : (
          <ul className="chat-threads__list">
            {threads.map((thread) => (
              <li key={thread.thread_id}>
                <button
                  type="button"
                  className={`chat-thread${thread.thread_id === threadId ? " chat-thread--active" : ""}`}
                  aria-current={thread.thread_id === threadId ? "true" : undefined}
                  disabled={sending || loadingThread}
                  onClick={() => void openThread(thread.thread_id)}
                >
                  Chat from {new Date(thread.created_at).toLocaleString()}
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>

      <ol className="chat-transcript" ref={transcriptRef} aria-label="Conversation" aria-live="polite">
        {turns.length === 0 && !sending ? (
          <li className="chat-muted chat-transcript__empty">
            Ask about this store&apos;s operations, for example: Analyze operations for 2026-03-03.
          </li>
        ) : null}
        {turns.map((turn) => (
          <TurnItem
            key={turn.turn_id}
            turn={turn}
            proposal={proposals.find((p) => p.turn_id === turn.turn_id) ?? null}
            apiKey={apiKey}
            track={track}
            onProposal={updateProposal}
          />
        ))}
        {pending ? (
          <>
            <li className="chat-msg chat-msg--user">
              <span className="chat-msg__author">You</span>
              <p className="chat-msg__text">{pending.message}</p>
            </li>
            <li className="chat-msg chat-msg--agent" role="status">
              <span className="chat-msg__author">Operations Agent</span>
              <p className="chat-msg__text chat-muted">Working on it…</p>
            </li>
          </>
        ) : null}
      </ol>

      {error ? <div className="notice notice--danger" role="alert">{error}</div> : null}

      <form className="chat-composer" onSubmit={onSubmit}>
        <label className="field__label" htmlFor="chat-message">Message</label>
        <textarea
          id="chat-message"
          className="input chat-composer__input"
          value={draft}
          maxLength={MAX_CHAT_MESSAGE_CHARS}
          rows={3}
          placeholder="Ask the Operations Agent…"
          disabled={sending}
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={onKeyDown}
        />
        <div className="chat-composer__footer">
          <span className="field__hint">{draft.length} / {MAX_CHAT_MESSAGE_CHARS} · Enter sends · Shift+Enter adds a line</span>
          <button type="submit" className="button" disabled={sending || draft.trim().length === 0}>
            {sending ? "Sending…" : failed !== null && failed.message === draft.trim() ? "Retry" : "Send"}
          </button>
        </div>
      </form>
    </div>
  );
}

function TurnItem({ turn, proposal, apiKey, track, onProposal }: {
  turn: ChatTurnView;
  proposal: ChatProposalView | null;
  apiKey: string;
  track: <T>(result: ProductResult<T>) => ProductResult<T>;
  onProposal: (proposal: ChatProposalView) => void;
}) {
  return (
    <>
      <li className="chat-msg chat-msg--user">
        <span className="chat-msg__author">You</span>
        <p className="chat-msg__text">{turn.user_text}</p>
      </li>
      <li className="chat-msg chat-msg--agent">
        <span className="chat-msg__author">Operations Agent</span>
        {turn.status === "completed" ? (
          <p className="chat-msg__text">{turn.assistant_text}</p>
        ) : (
          <p className="chat-msg__text chat-muted">
            {turn.status === "pending" ? "Still in progress." : turn.failure === "agent_disabled"
              ? "No answer: the Operations Agent is disabled."
              : "No answer: the run failed."}
          </p>
        )}
        {proposal ? <ProposalCard proposal={proposal} apiKey={apiKey} track={track} onProposal={onProposal} /> : null}
      </li>
    </>
  );
}

const PROPOSAL_STATE: Record<string, [string, Tone]> = {
  proposed: ["Waiting for your confirmation", "pending"],
  confirming: ["Confirmation in progress", "attention"],
  submitted: ["Submitted", "neutral"],
  cancelled: ["Cancelled", "neutral"],
};

function ticketOutcome(ticket: ChatTicketView): [string, Tone] {
  if (ticket.status === "verified") return ["Ticket created", "success"];
  if (ticket.status === "in_progress") return ["Not confirmed yet", "pending"];
  if (ticket.status === "requires_human") return ["Needs human review: not confirmed", "attention"];
  if (ticket.status === "awaiting_approval") return ["Awaiting approval: not created", "attention"];
  return [`Not created (${ticket.status}${ticket.reason ? `: ${ticket.reason}` : ""})`, "danger"];
}

function ProposalCard({ proposal, apiKey, track, onProposal }: {
  proposal: ChatProposalView;
  apiKey: string;
  track: <T>(result: ProductResult<T>) => ProductResult<T>;
  onProposal: (proposal: ChatProposalView) => void;
}) {
  // One Idempotency-Key per proposal confirmation attempt, reused for its retries.
  const keyRef = useRef<string | null>(null);
  const [ticket, setTicket] = useState<ChatTicketView | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [checked, setChecked] = useState<string | null>(null);

  const confirm = async () => {
    if (busy) return;
    setBusy(true);
    setError(null);
    keyRef.current ??= newIdempotencyKey();
    const result = track(await confirmTicketProposal(apiKey, proposal.proposal_id, keyRef.current));
    setBusy(false);
    if (!result.ok) {
      setError(describe(result));
      return;
    }
    setTicket(result.data.ticket);
    onProposal(result.data.proposal);
  };

  const cancel = async () => {
    if (busy) return;
    setBusy(true);
    setError(null);
    const result = track(await cancelTicketProposal(apiKey, proposal.proposal_id));
    setBusy(false);
    if (!result.ok) {
      setError(describe(result));
      return;
    }
    onProposal(result.data.proposal);
  };

  // An explicit, one-shot status read of the durable command (never polling).
  const checkStatus = async () => {
    if (busy || proposal.command_id === null) return;
    setBusy(true);
    const result = track(await getTicketCommand(apiKey, proposal.command_id));
    setBusy(false);
    if (!result.ok) {
      setError(describe(result));
      return;
    }
    setChecked(result.data.status);
  };

  const [stateText, stateTone] = PROPOSAL_STATE[proposal.state] ?? [proposal.state, "neutral" as Tone];
  const outcome = ticket ? ticketOutcome(ticket) : null;
  return (
    <div className="chat-proposal" aria-label="Ticket proposal">
      <div className="chat-proposal__header">
        <strong>Proposed operational ticket</strong>
        <Badge tone={stateTone}>{stateText}</Badge>
      </div>
      <p className="chat-proposal__label">Title</p>
      <p className="chat-msg__text">{proposal.title}</p>
      <p className="chat-proposal__label">Description</p>
      <p className="chat-msg__text">{proposal.description}</p>
      {outcome ? (
        <p className="chat-proposal__outcome">
          <Badge tone={outcome[1]}>{outcome[0]}</Badge>
          {ticket?.status === "verified" && ticket.ticket_id ? <> Ticket <Mono>{ticket.ticket_id}</Mono></> : null}
        </p>
      ) : null}
      {checked ? (
        <p className="chat-proposal__outcome">
          Command status: <Badge tone={checked === "verified" ? "success" : "neutral"}>
            {checked === "verified" ? "Ticket created" : checked}
          </Badge>
        </p>
      ) : null}
      {error ? <div className="notice notice--danger" role="alert">{error}</div> : null}
      <div className="chat-proposal__actions">
        {proposal.state === "proposed" || proposal.state === "confirming" ? (
          <button type="button" className="button button--small" disabled={busy} onClick={() => void confirm()}>
            {proposal.state === "confirming" || keyRef.current !== null ? "Retry confirmation" : "Confirm and create ticket"}
          </button>
        ) : null}
        {proposal.state === "proposed" ? (
          <button type="button" className="button button--ghost button--small" disabled={busy} onClick={() => void cancel()}>
            Cancel
          </button>
        ) : null}
        {proposal.state === "submitted" && proposal.command_id && !ticket ? (
          <button type="button" className="button button--ghost button--small" disabled={busy} onClick={() => void checkStatus()}>
            Check ticket status
          </button>
        ) : null}
      </div>
    </div>
  );
}
