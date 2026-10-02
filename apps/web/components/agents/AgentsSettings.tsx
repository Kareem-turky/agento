"use client";

// Settings → Agents: trusted Product Agent lifecycle management over the Product API
// (never AgentOS). It lists the Product Agents installed in this build with their manifest
// and effective state, and offers only Enable, Disable and Reset to default. There is no
// prompt, instruction, model, tool, permission or code editor, and no way to create an
// Agent: Agent definitions are Product source code.
//
// The Product API key lives only in this component's memory; every value is rendered as
// plain text.
import Link from "next/link";
import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import { listAgents, resetAgentConfiguration, setAgentEnabled } from "../../lib/product-api/client";
import type { AgentView, ProductErrorKind, ProductResult } from "../../lib/product-api/types";
import { Badge, Card, ErrorNotice, Mono, Timestamp, type Tone } from "../console/ui";

const AVAILABILITY: Record<string, [string, Tone]> = {
  available: ["Available", "success"],
  disabled: ["Disabled", "neutral"],
  unavailable: ["Unavailable", "attention"],
};

/** Fixed, safe labels for the Product's availability reason codes. */
const REASON: Record<string, string> = {
  disabled_by_configuration: "Disabled by this installation's Agent configuration.",
  runtime_not_composed: "Enabled, but this deployment did not compose its runtime (for example, no business backend).",
};

function errorText(kind: ProductErrorKind): string {
  switch (kind) {
    case "unauthenticated":
      return "Product API key not accepted";
    case "forbidden":
      return "Permission denied (agents.read is needed to view, agents.manage to change)";
    case "not_found":
      return "Agent not found";
    case "invalid":
      return "Invalid request";
    case "conflict":
      return "The operation did not complete; nothing was changed";
    case "service_unavailable":
      return "Agent management unavailable";
    default:
      return "Product API unavailable";
  }
}

export function AgentsSettings() {
  // Memory only: reloading or leaving the page forgets the key.
  const [apiKey, setApiKey] = useState<string | null>(null);
  const [epoch, setEpoch] = useState(0);
  const [draft, setDraft] = useState("");

  function applyKey(event: FormEvent) {
    event.preventDefault();
    const key = draft.trim();
    if (!key) return;
    setApiKey(key);
    setEpoch((value) => value + 1);
    setDraft("");
  }

  function disconnect() {
    setApiKey(null);
    setEpoch((value) => value + 1);
  }

  return (
    <div className="shell">
      <header className="topbar">
        <div className="topbar__brand">
          <span className="topbar__mark" aria-hidden="true">◆</span>
          <div>
            <p className="topbar__title">Agents</p>
            <p className="topbar__subtitle">Settings · Product Agent management over the Product API</p>
          </div>
        </div>
        <nav className="topbar__nav" aria-label="Pages">
          <Link href="/">Operations Console</Link>
          <Link href="/settings/integrations">Integrations</Link>
        </nav>
      </header>

      <div className="layout">
        <aside className="layout__side">
          <Card title="Session" subtitle="Held in this page's memory only. Reloading or leaving the page forgets the key.">
            <form className="form" onSubmit={applyKey} autoComplete="off">
              <label className="field">
                <span className="field__label">Product API key</span>
                <input
                  type="password"
                  name="product-api-key"
                  autoComplete="off"
                  spellCheck={false}
                  value={draft}
                  onChange={(event) => setDraft(event.target.value)}
                  placeholder={apiKey === null ? "Paste your Product API key" : "Replace the key in memory"}
                />
                <span className="field__hint">Viewing needs agents.read; changes need agents.manage.</span>
              </label>
              <button type="submit" className="button" disabled={!draft.trim()}>Use key</button>
            </form>
            <button type="button" className="button button--danger" onClick={disconnect} disabled={apiKey === null}>
              Disconnect and clear session
            </button>
          </Card>
        </aside>

        <main className="layout__main" key={epoch}>
          {apiKey === null ? (
            <div className="notice notice--neutral">Set a Product API key in <strong>Session</strong> first.</div>
          ) : (
            <AgentsWorkspace apiKey={apiKey} />
          )}
        </main>
      </div>
    </div>
  );
}

function AgentsWorkspace({ apiKey }: { apiKey: string }) {
  const [agents, setAgents] = useState<AgentView[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const busyRef = useRef(false);

  const load = useCallback(async () => {
    setLoadError(null);
    const result = await listAgents(apiKey);
    if (!result.ok) {
      setLoadError(errorText(result.error));
      return;
    }
    setAgents(result.data.agents);
  }, [apiKey]);

  useEffect(() => {
    void load();
  }, [load]);

  /** One mutation at a time, never retried automatically; the list is reloaded after. */
  async function run<T>(action: () => Promise<ProductResult<T>>, success: string) {
    if (busyRef.current) return;
    busyRef.current = true;
    setBusy(true);
    setActionError(null);
    setNotice(null);
    try {
      const result = await action();
      if (!result.ok) {
        setActionError(errorText(result.error));
        return;
      }
      setNotice(success);
      await load();
    } finally {
      busyRef.current = false;
      setBusy(false);
    }
  }

  return (
    <>
      {loadError ? <ErrorNotice message={loadError} /> : null}
      {actionError ? <ErrorNotice message={actionError} /> : null}
      {notice ? <div className="notice notice--neutral" role="status">{notice}</div> : null}

      <Card
        title="Product Agents"
        subtitle="The Product Agents installed in this build. Their behaviour is trusted Product code; this page only turns them on or off for this installation."
        actions={<button type="button" className="button button--ghost button--small" onClick={() => void load()}
                         disabled={busy}>Refresh</button>}
      >
        {agents === null ? (
          <p className="loading">{loadError ? "Not loaded." : "Loading…"}</p>
        ) : agents.length === 0 ? (
          <p className="empty">No Product Agents are installed in this build.</p>
        ) : (
          <ul className="integration-list">
            {agents.map(({ definition, state }) => {
              const [label, tone] = AVAILABILITY[state.availability] ?? ["Unknown", "neutral"];
              const id = definition.agent_id;
              const manifest = definition.manifest;
              return (
                <li key={id} className="integration-list__item integration-list__item--connection">
                  <div className="integration-list__body">
                    <p className="integration-list__title">
                      <strong>{definition.name}</strong>{" "}
                      <Badge tone={state.enabled ? "success" : "neutral"}>{state.enabled ? "Enabled" : "Disabled"}</Badge>
                      {/* Availability adds information only when the Agent is enabled. */}
                      {state.enabled ? <>{" "}<Badge tone={tone}>{label}</Badge></> : null}
                    </p>
                    <p className="field__hint">
                      <Mono>{id}</Mono> · {definition.category} · {definition.lifecycle}
                    </p>
                    <p className="form__context">{definition.description}</p>
                    {state.reason ? <p className="field__hint">{REASON[state.reason] ?? "Not available."}</p> : null}
                    <p className="field__hint">
                      Configuration: {state.source === "override" ? "set for this installation" : "Product default"}
                      {state.updated_at ? <> · changed <Timestamp value={state.updated_at} /></> : null}
                      {" "}· default {definition.default_enabled ? "enabled" : "disabled"}
                    </p>
                    <p className="field__hint">Capabilities: {definition.capabilities.join(", ") || "none"}</p>
                    <details className="manifest">
                      <summary>Manifest (descriptive; Product governance still decides every action)</summary>
                      <p className="field__hint">Tool-call limit: {manifest.tool_call_limit}</p>
                      <ul className="manifest__tools">
                        {manifest.tools.map((tool) => (
                          <li key={tool.tool_id}>
                            <Mono>{tool.tool_id}</Mono>{" "}
                            <Badge tone={tool.access === "write" ? "attention" : "neutral"}>{tool.access}</Badge>{" "}
                            {tool.description} <span className="field__hint">({tool.action_names.join(", ")})</span>
                          </li>
                        ))}
                      </ul>
                      <p className="field__hint">Requires: {manifest.requirements.join(", ") || "nothing"}</p>
                      <p className="field__hint">Safety: {manifest.safety.join(", ") || "none declared"}</p>
                    </details>
                  </div>
                  <div className="form__actions">
                    {state.enabled ? (
                      <button type="button" className="button button--danger button--small" disabled={busy}
                              onClick={() => void run(() => setAgentEnabled(apiKey, id, false), `${definition.name} disabled.`)}>
                        Disable
                      </button>
                    ) : (
                      <button type="button" className="button button--secondary button--small" disabled={busy}
                              onClick={() => void run(() => setAgentEnabled(apiKey, id, true), `${definition.name} enabled.`)}>
                        Enable
                      </button>
                    )}
                    {state.source === "override" ? (
                      <button type="button" className="button button--ghost button--small" disabled={busy}
                              onClick={() => void run(() => resetAgentConfiguration(apiKey, id), `${definition.name} reset to its Product default.`)}>
                        Reset to default
                      </button>
                    ) : null}
                  </div>
                </li>
              );
            })}
          </ul>
        )}
      </Card>
    </>
  );
}
