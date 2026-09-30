"use client";

import { useRef, useState, type FormEvent } from "react";
import { looksLikeUuid, runOperations } from "../../lib/product-api/client";
import type { OperationsRunResponse, ProductResult } from "../../lib/product-api/types";
import { Badge, Card, ErrorNotice, KeyValue, Mono, NeedsSession, errorMessage } from "./ui";

export const MAX_MESSAGE_LENGTH = 8000;

export function AnalysisPanel({ apiKey, storeId, onAuthResult }: {
  apiKey: string | null;
  storeId: string;
  onAuthResult: (result: ProductResult<unknown>) => void;
}) {
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const busyRef = useRef(false);
  const [result, setResult] = useState<OperationsRunResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const ready = apiKey !== null && looksLikeUuid(storeId);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (apiKey === null || !looksLikeUuid(storeId) || busyRef.current || !message.trim()) return;
    busyRef.current = true;
    setBusy(true);
    setError(null);
    const response = await runOperations(apiKey, storeId, message);
    busyRef.current = false;
    setBusy(false);
    onAuthResult(response);
    if (response.ok) {
      setResult(response.data);
    } else {
      setResult(null);
      setError(errorMessage(response.error, "analysis"));
    }
  }

  return (
    <Card
      title="Read-only Operations analysis"
      subtitle="The Operations Agent answers from store data. This surface never creates tickets or changes anything."
      actions={<Badge tone="neutral">Read-only</Badge>}
    >
      {!ready ? <NeedsSession /> : null}
      <form className="form" onSubmit={submit}>
        <p className="form__context">Store <Mono>{storeId || "not set"}</Mono></p>
        <label className="field">
          <span className="field__label">Question for the Operations Agent</span>
          <textarea
            name="message"
            rows={6}
            maxLength={MAX_MESSAGE_LENGTH}
            value={message}
            onChange={(event) => setMessage(event.target.value)}
            placeholder="For example: Which of today's shipments need attention?"
            required
          />
          <span className="field__hint">{message.length} / {MAX_MESSAGE_LENGTH} characters</span>
        </label>
        <div className="form__actions">
          <button type="submit" className="button" disabled={!ready || busy || !message.trim()}>
            {busy ? "Analyzing…" : "Run read-only analysis"}
          </button>
        </div>
      </form>

      <div aria-live="polite" aria-busy={busy}>
        {busy ? <p className="loading">Waiting for the Operations Agent…</p> : null}
        {error ? <ErrorNotice message={error} /> : null}
        {result ? (
          <div className="result">
            <h3 className="result__title">Analysis</h3>
            <div className="prose">{result.message}</div>
            <KeyValue items={[["Request ID", <Mono key="r">{result.request_id}</Mono>]]} />
          </div>
        ) : null}
      </div>
    </Card>
  );
}
