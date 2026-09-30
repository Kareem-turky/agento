"use client";

import { useEffect, useRef, useState, type FormEvent } from "react";
import { createTicket, looksLikeUuid, newIdempotencyKey } from "../../lib/product-api/client";
import type { ProductResult, TicketCreateResponse } from "../../lib/product-api/types";
import { TicketStatus, ticketStatusExplanation } from "./ticketStatus";
import { Badge, Card, ErrorNotice, KeyValue, Mono, NeedsSession, errorMessage } from "./ui";

export const MAX_TITLE_LENGTH = 160;
export const MAX_DESCRIPTION_LENGTH = 4000;

type Pending = { key: string; intent: string };

export function TicketPanel({ epoch, apiKey, storeId, onAuthResult, onCommand }: {
  apiKey: string | null;
  storeId: string;
  /** The session epoch this panel instance belongs to. */
  epoch: number;
  onAuthResult: (epoch: number, result: ProductResult<unknown>) => void;
  onCommand: (epoch: number, commandId: string) => void;
}) {
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [busy, setBusy] = useState(false);
  const busyRef = useRef(false);
  const [result, setResult] = useState<TicketCreateResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [retryable, setRetryable] = useState(false);
  // One idempotency key per ticket intent, in memory only; never shown or logged.
  const [pending, setPending] = useState<Pending | null>(null);
  const ready = apiKey !== null && looksLikeUuid(storeId);
  const intent = JSON.stringify([storeId, title, description]);
  const canRetry = retryable && pending !== null && pending.intent === intent;

  // A changed intent (store, title or description) discards the old key: the next
  // submission is a new request with a new key.
  function discardKey() {
    setPending(null);
    setRetryable(false);
  }
  useEffect(() => {
    setPending((current) => (current && current.intent !== intent ? null : current));
  }, [intent]);

  async function submit(event?: FormEvent) {
    event?.preventDefault();
    if (apiKey === null || !looksLikeUuid(storeId) || busyRef.current) return;
    if (!title.trim() || !description.trim()) return;
    // The same unchanged intent reuses its key (a safe replay); anything else is new.
    const key = pending && pending.intent === intent ? pending.key : newIdempotencyKey();
    setPending({ key, intent });
    busyRef.current = true;
    setBusy(true);
    setError(null);
    setRetryable(false);
    // Exactly one POST per click; never retried automatically.
    const response = await createTicket(apiKey, { storeId, title, description }, key);
    busyRef.current = false;
    setBusy(false);
    onAuthResult(epoch, response);
    if (response.ok) {
      setResult(response.data);
      onCommand(epoch, response.data.command_id);
    } else {
      setResult(null);
      setError(errorMessage(response.error, "ticket"));
      // Ambiguous outcome: the command may or may not exist. Keep the same key so a
      // manual retry of this unchanged request is replayed, not duplicated.
      setRetryable(response.error === "unavailable" || response.error === "service_unavailable");
    }
  }

  function reset() {
    if (busyRef.current) return;
    discardKey();
    setTitle("");
    setDescription("");
    setResult(null);
    setError(null);
  }

  return (
    <Card
      title="Operational ticket"
      subtitle="An explicit, deterministic Product write. It never goes through the Operations Agent."
      actions={<Badge tone="attention">Write</Badge>}
    >
      {!ready ? <NeedsSession /> : null}
      <form className="form" onSubmit={submit}>
        <p className="form__context">Store <Mono>{storeId || "not set"}</Mono></p>
        <label className="field">
          <span className="field__label">Title</span>
          <input name="title" value={title} maxLength={MAX_TITLE_LENGTH} required disabled={busy}
                 onChange={(event) => { discardKey(); setTitle(event.target.value); }} />
          <span className="field__hint">{title.length} / {MAX_TITLE_LENGTH} characters</span>
        </label>
        <label className="field">
          <span className="field__label">Description</span>
          <textarea name="description" rows={6} value={description} maxLength={MAX_DESCRIPTION_LENGTH}
                    required disabled={busy} onChange={(event) => { discardKey(); setDescription(event.target.value); }} />
          <span className="field__hint">{description.length} / {MAX_DESCRIPTION_LENGTH} characters</span>
        </label>
        <div className="form__actions">
          <button type="submit" className="button" disabled={!ready || busy || !title.trim() || !description.trim()}>
            {busy ? "Creating…" : "Create operational ticket"}
          </button>
          {canRetry ? (
            <button type="button" className="button button--secondary" disabled={busy} onClick={() => submit()}>
              Retry same ticket request
            </button>
          ) : null}
          <button type="button" className="button button--ghost" disabled={busy} onClick={reset}>
            Reset ticket form
          </button>
        </div>
      </form>

      <div aria-live="polite" aria-busy={busy}>
        {busy ? <p className="loading">Submitting the ticket command…</p> : null}
        {error ? <ErrorNotice message={error} /> : null}
        {canRetry ? (
          <p className="notice notice--attention">
            The outcome is unknown. “Retry same ticket request” repeats this exact request so the
            Product can recognise it instead of creating a duplicate. Changing the form starts a new request.
          </p>
        ) : null}
        {result ? (
          <div className="result">
            <h3 className="result__title">
              Ticket command <TicketStatus status={result.status} />
            </h3>
            <p className="result__explain">{ticketStatusExplanation(result.status)}</p>
            {!result.persistence_complete ? (
              <p className="notice notice--attention">
                The durable command record was not fully saved. Check the command status later.
              </p>
            ) : null}
            <KeyValue items={[
              ["Status", <Mono key="s">{result.status}</Mono>],
              ["Reason", result.reason ? <Mono key="re">{result.reason}</Mono> : "—"],
              ["Ticket ID", result.ticket_id ? <Mono key="t">{result.ticket_id}</Mono> : "Not returned"],
              ["Command ID", <Mono key="c">{result.command_id}</Mono>],
              ["Replayed", result.replayed ? "Yes (an earlier identical request)" : "No"],
              ["Persistence complete", result.persistence_complete ? "Yes" : "No"],
              ["Request ID", <Mono key="r">{result.request_id}</Mono>],
            ]} />
          </div>
        ) : null}
      </div>
    </Card>
  );
}
