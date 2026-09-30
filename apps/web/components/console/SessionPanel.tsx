"use client";

import { useState, type FormEvent } from "react";
import { looksLikeUuid } from "../../lib/product-api/client";
import type { KeyStatus } from "./session";
import { Badge, Card } from "./ui";

export type HealthState = "checking" | "reachable" | "unavailable";

const KEY_STATUS: Record<KeyStatus, [string, "neutral" | "pending" | "success" | "danger"]> = {
  unset: ["No key set", "neutral"],
  unverified: ["Set, not yet used", "pending"],
  accepted: ["Accepted", "success"], // only after a Product request succeeded
  rejected: ["Not accepted", "danger"],
};

export function SessionPanel({
  keyStatus,
  storeId,
  health,
  onUseKey,
  onStoreChange,
  onCheckHealth,
  onDisconnect,
}: {
  keyStatus: KeyStatus;
  storeId: string;
  health: HealthState;
  onUseKey: (key: string) => void;
  onStoreChange: (storeId: string) => void;
  onCheckHealth: () => void;
  onDisconnect: () => void;
}) {
  // The draft lives only in this input until it is handed to the console's memory;
  // then the field is emptied so the raw key is never shown again.
  const [draft, setDraft] = useState("");
  const storeValid = storeId === "" || looksLikeUuid(storeId);
  const [keyLabel, keyTone] = KEY_STATUS[keyStatus];

  function submit(event: FormEvent) {
    event.preventDefault();
    const key = draft.trim();
    if (!key) return;
    onUseKey(key);
    setDraft("");
  }

  return (
    <Card
      title="Session"
      subtitle="Held in this page's memory only. Reloading the page forgets the key."
    >
      <div className="status-list" aria-live="polite">
        <div className="status-list__row">
          <span>Product API</span>
          {health === "checking" ? <Badge tone="pending">Checking…</Badge>
            : health === "reachable" ? <Badge tone="success">API reachable</Badge>
            : <Badge tone="danger">API unavailable</Badge>}
        </div>
        <div className="status-list__row">
          <span>Product API key</span>
          <Badge tone={keyTone}>{keyLabel}</Badge>
        </div>
        <div className="status-list__row">
          <span>Store UUID</span>
          {storeId && storeValid ? <Badge tone="success">Configured</Badge>
            : <Badge tone="neutral">{storeId ? "Not a UUID" : "Not set"}</Badge>}
        </div>
      </div>
      <button type="button" className="button button--ghost button--small" onClick={onCheckHealth}
              disabled={health === "checking"}>
        Check API reachability
      </button>

      <form className="form" onSubmit={submit} autoComplete="off">
        <label className="field">
          <span className="field__label">Product API key</span>
          <input
            type="password"
            name="product-api-key"
            autoComplete="off"
            spellCheck={false}
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            placeholder={keyStatus === "unset" ? "Paste your Product API key" : "Replace the key in memory"}
          />
          <span className="field__hint">Sent only as an Authorization header to this console’s Product proxy.</span>
        </label>
        <button type="submit" className="button" disabled={!draft.trim()}>Use key</button>
      </form>

      <label className="field">
        <span className="field__label">Store UUID</span>
        <input
          name="store-id"
          inputMode="text"
          spellCheck={false}
          value={storeId}
          onChange={(event) => onStoreChange(event.target.value.trim())}
          placeholder="00000000-0000-4000-8000-000000000000"
          aria-invalid={!storeValid}
          aria-describedby="store-id-hint"
        />
        <span className="field__hint" id="store-id-hint">
          {storeValid ? "The Product API decides whether this key may access the store."
            : "Enter a UUID (format check only)."}
        </span>
      </label>

      <button type="button" className="button button--danger" onClick={onDisconnect}
              disabled={keyStatus === "unset" && !storeId}>
        Disconnect and clear session
      </button>
    </Card>
  );
}
