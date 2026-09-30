"use client";

import { useRef, useState, type FormEvent } from "react";
import { getTicketCommand, looksLikeUuid } from "../../lib/product-api/client";
import type { ProductResult, TicketCommandStatusResponse } from "../../lib/product-api/types";
import { TicketStatus, ticketStatusExplanation } from "./ticketStatus";
import { Badge, Card, ErrorNotice, KeyValue, Mono, Timestamp, errorMessage } from "./ui";

export function CommandPanel({ apiKey, recentCommandId, onAuthResult }: {
  apiKey: string | null;
  recentCommandId: string | null;
  onAuthResult: (result: ProductResult<unknown>) => void;
}) {
  const [commandId, setCommandId] = useState("");
  const [seenRecent, setSeenRecent] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const busyRef = useRef(false);
  const [result, setResult] = useState<TicketCommandStatusResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Auto-fill from the most recent ticket submission (once per new command).
  if (recentCommandId && recentCommandId !== seenRecent) {
    setSeenRecent(recentCommandId);
    setCommandId(recentCommandId);
  }

  const valid = looksLikeUuid(commandId);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (apiKey === null || !looksLikeUuid(commandId) || busyRef.current) return;
    busyRef.current = true;
    setBusy(true);
    setError(null);
    // One manual read per click: no polling, no background refresh.
    const response = await getTicketCommand(apiKey, commandId.trim());
    busyRef.current = false;
    setBusy(false);
    onAuthResult(response);
    if (response.ok) {
      setResult(response.data);
    } else {
      setResult(null);
      setError(errorMessage(response.error, "command"));
    }
  }

  return (
    <Card
      title="Ticket command status"
      subtitle="The durable state of a ticket command. Reading it never submits or retries anything."
      actions={<Badge tone="neutral">Read-only</Badge>}
    >
      {apiKey === null ? (
        <div className="notice notice--neutral">Set a Product API key in <strong>Session</strong> first.</div>
      ) : null}
      <form className="form form--inline" onSubmit={submit}>
        <label className="field field--wide">
          <span className="field__label">Command ID</span>
          <input name="command_id" spellCheck={false} value={commandId}
                 onChange={(event) => setCommandId(event.target.value.trim())}
                 aria-invalid={commandId !== "" && !valid}
                 placeholder="Filled in from your most recent ticket request" />
        </label>
        <div className="form__actions">
          <button type="submit" className="button" disabled={apiKey === null || !valid || busy}>
            {busy ? "Refreshing…" : "Refresh command status"}
          </button>
        </div>
      </form>

      <div aria-live="polite" aria-busy={busy}>
        {busy ? <p className="loading">Reading the command status…</p> : null}
        {error ? <ErrorNotice message={error} /> : null}
        {result ? (
          <div className="result">
            <h3 className="result__title">Command <TicketStatus status={result.status} /></h3>
            <p className="result__explain">{ticketStatusExplanation(result.status)}</p>
            <KeyValue items={[
              ["Command ID", <Mono key="c">{result.command_id}</Mono>],
              ["Status", <Mono key="s">{result.status}</Mono>],
              ["Reason", result.reason ? <Mono key="re">{result.reason}</Mono> : "—"],
              ["Ticket ID", result.ticket_id ? <Mono key="t">{result.ticket_id}</Mono> : "Not returned"],
              ["Created at", <Timestamp key="ca" value={result.created_at} />],
              ["Updated at", <Timestamp key="ua" value={result.updated_at} />],
              ["Request ID", <Mono key="r">{result.request_id}</Mono>],
            ]} />
          </div>
        ) : null}
      </div>
    </Card>
  );
}
