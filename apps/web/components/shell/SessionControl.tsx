"use client";

// The shell's one session control: API reachability, key status, connect / replace and
// disconnect. The key draft lives only in the password input until it is handed to the
// session's memory; then the field is emptied, so the raw key is never shown again.
import { useState, type FormEvent } from "react";
import { Badge, type Tone } from "../ui/primitives";
import type { KeyStatus } from "./session";
import { useProductSession, type HealthState } from "./ProductSessionProvider";

const KEY_STATUS: Record<KeyStatus, [string, Tone]> = {
  unset: ["Not connected", "neutral"],
  unverified: ["Connected, not yet verified", "pending"],
  accepted: ["Key accepted", "success"], // only after a Product request succeeded
  rejected: ["Key not accepted", "danger"],
};

const HEALTH: Record<HealthState, [string, Tone]> = {
  checking: ["Checking", "pending"],
  online: ["API online", "success"],
  unavailable: ["API unavailable", "danger"],
};

export function HealthBadge() {
  const { health } = useProductSession();
  const [label, tone] = HEALTH[health];
  return <Badge tone={tone}>{label}</Badge>;
}

export function KeyStatusBadge() {
  const { keyStatus } = useProductSession();
  const [label, tone] = KEY_STATUS[keyStatus];
  return <Badge tone={tone}>{label}</Badge>;
}

/** The password form that hands a key to the session (used by the shell and Overview). */
export function ConnectForm({ idPrefix, submitLabel = "Connect" }: { idPrefix: string; submitLabel?: string }) {
  const { connect, keyStatus } = useProductSession();
  const [draft, setDraft] = useState("");
  const inputId = `${idPrefix}-product-api-key`;

  function submit(event: FormEvent) {
    event.preventDefault();
    const key = draft.trim();
    if (!key) return;
    connect(key);
    setDraft("");
  }

  return (
    <form className="form" onSubmit={submit} autoComplete="off">
      <div className="field">
        <label className="field__label" htmlFor={inputId}>Product API key</label>
        <input
          id={inputId}
          type="password"
          name="product-api-key"
          autoComplete="off"
          spellCheck={false}
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          placeholder={keyStatus === "unset" ? "Paste your Product API key" : "Replace the key in memory"}
        />
        <span className="field__hint">Held in this tab’s memory only. Reloading the page forgets it.</span>
      </div>
      <button type="submit" className="button" disabled={!draft.trim()}>{submitLabel}</button>
    </form>
  );
}

export function SessionControl() {
  const { keyStatus, health, checkHealth, disconnect } = useProductSession();

  return (
    <section className="session-control" aria-label="Session">
      <h2 className="session-control__title">Session</h2>
      <div className="status-list" aria-live="polite">
        <div className="status-list__row">
          <span>Product API</span>
          <HealthBadge />
        </div>
        <div className="status-list__row">
          <span>Key</span>
          <KeyStatusBadge />
        </div>
      </div>
      <button type="button" className="button button--ghost button--small" onClick={checkHealth}
              disabled={health === "checking"}>
        Check API
      </button>
      {keyStatus === "unset" ? (
        <ConnectForm idPrefix="shell" />
      ) : (
        <>
          <details className="session-control__replace">
            <summary>Replace key</summary>
            <ConnectForm idPrefix="shell-replace" submitLabel="Use this key" />
          </details>
          <button type="button" className="button button--danger button--small" onClick={disconnect}>
            Disconnect
          </button>
        </>
      )}
    </section>
  );
}
