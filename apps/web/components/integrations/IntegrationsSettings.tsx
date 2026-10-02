"use client";

// Settings → Integrations: a generic Connections UI over the Product API. It shows the
// integrations INSTALLED in this build (the Product's static catalog, grouped by
// category) and this company's connection METADATA. Nothing here is provider-specific:
// there are no hard-coded provider cards, and an empty catalog is a normal state.
//
// The Product API key lives only in this component's memory (like the Operations
// Console); secret field values exist only inside the form that sends them.
import Link from "next/link";
import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import {
  createIntegrationConnection,
  deleteIntegrationConnection,
  getIntegrationCatalog,
  listIntegrationConnections,
  replaceIntegrationCredentials,
  setIntegrationConnectionEnabled,
  testIntegrationConnection,
  updateIntegrationConnection,
} from "../../lib/product-api/client";
import type {
  IntegrationConnectionView,
  IntegrationDefinitionView,
  ProductErrorKind,
  ProductResult,
} from "../../lib/product-api/types";
import { Badge, Card, ErrorNotice, Mono, Timestamp, type Tone } from "../console/ui";
import { ConnectionForm, type ConnectionFormMode, type ConnectionFormSubmission } from "./ConnectionForm";

const CATEGORIES: [string, string][] = [
  ["commerce", "Commerce"],
  ["messaging", "Messaging"],
  ["marketing", "Marketing"],
  ["shipping", "Shipping"],
  ["accounting", "Accounting"],
];

const TEST_RESULT: Record<string, [string, Tone]> = {
  never_tested: ["Never tested", "neutral"],
  success: ["Last test succeeded", "success"],
  failure: ["Last test failed", "danger"],
};

/** Fixed, safe labels for the Product's connection error classification. */
const TEST_ERROR: Record<string, string> = {
  authentication_failed: "Authentication failed",
  permission_denied: "Permission denied by the external system",
  unreachable: "External system unreachable",
  timeout: "Timed out",
  invalid_configuration: "Invalid configuration",
  credentials_unavailable: "Stored credentials unavailable",
  unexpected_response: "Unexpected response",
  provider_error: "External system error",
};

function errorText(kind: ProductErrorKind): string {
  switch (kind) {
    case "unauthenticated":
      return "Product API key not accepted";
    case "forbidden":
      return "Permission denied (integrations.read is needed to view, integrations.manage to change)";
    case "not_found":
      return "Integration connection not found";
    case "invalid":
      return "Invalid connection settings";
    case "conflict":
      return "The operation did not complete; nothing was changed";
    case "too_large":
      return "Request too large";
    case "service_unavailable":
      return "Integration management unavailable";
    default:
      return "Product API unavailable";
  }
}

type Editor =
  | { mode: "create"; integrationId: string }
  | { mode: Exclude<ConnectionFormMode, "create">; integrationId: string; connectionId: string };

export function IntegrationsSettings() {
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
            <p className="topbar__title">Integrations</p>
            <p className="topbar__subtitle">Settings · connection management over the Product API</p>
          </div>
        </div>
        <nav className="topbar__nav" aria-label="Pages">
          <Link href="/">Operations Console</Link>
          <Link href="/settings/agents">Agents</Link>
          <Link href="/settings/workflows">Workflows</Link>
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
                <span className="field__hint">Viewing needs integrations.read; changes need integrations.manage.</span>
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
            <IntegrationsWorkspace apiKey={apiKey} />
          )}
        </main>
      </div>
    </div>
  );
}

function IntegrationsWorkspace({ apiKey }: { apiKey: string }) {
  const [catalog, setCatalog] = useState<IntegrationDefinitionView[] | null>(null);
  const [connections, setConnections] = useState<IntegrationConnectionView[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [editor, setEditor] = useState<Editor | null>(null);
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const busyRef = useRef(false);

  const load = useCallback(async () => {
    setLoadError(null);
    const [catalogResult, connectionResult] = await Promise.all([
      getIntegrationCatalog(apiKey),
      listIntegrationConnections(apiKey),
    ]);
    if (!catalogResult.ok) {
      setLoadError(errorText(catalogResult.error));
      return;
    }
    setCatalog(catalogResult.data.integrations);
    if (!connectionResult.ok) {
      setLoadError(errorText(connectionResult.error));
      return;
    }
    setConnections(connectionResult.data.connections);
  }, [apiKey]);

  useEffect(() => {
    void load();
  }, [load]);

  /** One mutation at a time, never retried automatically; the list is reloaded after. */
  async function run<T>(action: () => Promise<ProductResult<T>>, success: string): Promise<boolean> {
    if (busyRef.current) return false;
    busyRef.current = true;
    setBusy(true);
    setActionError(null);
    setNotice(null);
    try {
      const result = await action();
      if (!result.ok) {
        setActionError(errorText(result.error));
        return false;
      }
      setNotice(success);
      await load();
      return true;
    } finally {
      busyRef.current = false;
      setBusy(false);
    }
  }

  async function submitEditor(submission: ConnectionFormSubmission): Promise<boolean> {
    if (editor === null) return false;
    let done: boolean;
    if (editor.mode === "create") {
      done = await run(() => createIntegrationConnection(apiKey, {
        integrationId: editor.integrationId,
        displayName: submission.displayName,
        config: submission.config,
        credentials: submission.credentials,
      }), "Connection created. It has not been tested yet.");
    } else if (editor.mode === "edit") {
      done = await run(() => updateIntegrationConnection(apiKey, editor.connectionId, {
        displayName: submission.displayName,
        config: submission.config,
      }), "Settings saved.");
    } else {
      done = await run(() => replaceIntegrationCredentials(apiKey, editor.connectionId, submission.credentials),
        "Credentials replaced.");
    }
    if (done) setEditor(null);
    return done;
  }

  const byId = new Map((catalog ?? []).map((definition) => [definition.integration_id, definition]));
  const editorDefinition = editor ? byId.get(editor.integrationId) : undefined;
  const editorConnection = editor && editor.mode !== "create"
    ? connections?.find((c) => c.connection_id === editor.connectionId) : undefined;

  return (
    <>
      {loadError ? <ErrorNotice message={loadError} /> : null}
      {actionError ? <ErrorNotice message={actionError} /> : null}
      {notice ? <div className="notice notice--neutral" role="status">{notice}</div> : null}

      <Card
        title="Installed integrations"
        subtitle="Integrations installed in this build of the Product. Categories are for grouping only; they grant nothing."
        actions={<button type="button" className="button button--ghost button--small" onClick={() => void load()}
                         disabled={busy}>Refresh</button>}
      >
        {catalog === null ? (
          <p className="loading">{loadError ? "Not loaded." : "Loading…"}</p>
        ) : catalog.length === 0 ? (
          <p className="empty">
            No integrations are installed in this build. Connections become available when an
            integration is added to the Product&apos;s reviewed catalog.
          </p>
        ) : (
          CATEGORIES.map(([category, label]) => {
            const items = catalog.filter((definition) => definition.category === category);
            if (items.length === 0) return null;
            return (
              <section key={category} aria-label={label}>
                <h3 className="section-title">{label}<span className="section-title__count">{items.length}</span></h3>
                <ul className="integration-list">
                  {items.map((definition) => (
                    <li key={definition.integration_id} className="integration-list__item">
                      <div>
                        <strong>{definition.name}</strong> <Mono>{definition.integration_id}</Mono>
                        <p className="form__context">{definition.description}</p>
                        {definition.capabilities.length > 0 ? (
                          <p className="field__hint">Declared capabilities: {definition.capabilities.join(", ")}</p>
                        ) : null}
                      </div>
                      {definition.connectable ? (
                        <button type="button" className="button button--secondary button--small" disabled={busy}
                                onClick={() => setEditor({ mode: "create", integrationId: definition.integration_id })}>
                          Connect
                        </button>
                      ) : (
                        <Badge tone="neutral">Not connectable in this build</Badge>
                      )}
                    </li>
                  ))}
                </ul>
              </section>
            );
          })
        )}
      </Card>

      {editor && editorDefinition ? (
        <Card title={editorDefinition.name} subtitle={<Mono>{editorDefinition.integration_id}</Mono>}>
          <ConnectionForm
            key={`${editor.mode}-${editor.integrationId}-${editor.mode === "create" ? "" : editor.connectionId}`}
            definition={editorDefinition}
            mode={editor.mode}
            connection={editorConnection}
            busy={busy}
            onSubmit={submitEditor}
            onCancel={() => setEditor(null)}
          />
        </Card>
      ) : null}

      <Card title="Connections" subtitle="Connection metadata for your company. Test results are the last known result only.">
        {connections === null ? (
          <p className="loading">{loadError ? "Not loaded." : "Loading…"}</p>
        ) : connections.length === 0 ? (
          <p className="empty">No connections yet.</p>
        ) : (
          <ul className="integration-list">
            {connections.map((connection) => {
              const definition = byId.get(connection.integration_id);
              const [resultLabel, resultTone] = TEST_RESULT[connection.last_test_result] ?? ["Unknown", "neutral"];
              const id = connection.connection_id;
              return (
                <li key={id} className="integration-list__item integration-list__item--connection">
                  <div className="integration-list__body">
                    <p className="integration-list__title">
                      <strong>{connection.display_name}</strong>{" "}
                      <Badge tone={connection.enabled ? "success" : "neutral"}>
                        {connection.enabled ? "Enabled" : "Disabled"}
                      </Badge>{" "}
                      <Badge tone={resultTone}>{resultLabel}</Badge>
                    </p>
                    <p className="field__hint">
                      {definition ? definition.name : "Integration not installed in this build"} ·{" "}
                      <Mono>{connection.integration_id}</Mono> · <Mono>{id}</Mono>
                    </p>
                    <p className="field__hint">
                      Last known test:{" "}
                      {connection.last_tested_at ? <Timestamp value={connection.last_tested_at} /> : "never"}
                      {connection.last_test_error ? ` · ${TEST_ERROR[connection.last_test_error] ?? "Failed"}` : ""}
                    </p>
                    {Object.keys(connection.config).length > 0 ? (
                      <p className="field__hint">
                        Settings:{" "}
                        {Object.entries(connection.config)
                          .map(([name, value]) => `${name}: ${typeof value === "boolean" ? (value ? "yes" : "no") : value}`)
                          .join(" · ")}
                      </p>
                    ) : null}
                    {connection.configured_secret_fields.length > 0 ? (
                      <p className="field__hint">
                        Credentials configured: {connection.configured_secret_fields.join(", ")} (values are never shown)
                      </p>
                    ) : null}
                  </div>
                  <div className="form__actions">
                    <button type="button" className="button button--secondary button--small" disabled={busy || !definition}
                            onClick={() => void run(() => testIntegrationConnection(apiKey, id), "Connection test finished.")}>
                      Test connection
                    </button>
                    <button type="button" className="button button--ghost button--small" disabled={busy}
                            onClick={() => void run(() => setIntegrationConnectionEnabled(apiKey, id, !connection.enabled),
                              connection.enabled ? "Connection disabled." : "Connection enabled.")}>
                      {connection.enabled ? "Disable" : "Enable"}
                    </button>
                    <button type="button" className="button button--ghost button--small" disabled={busy || !definition}
                            onClick={() => setEditor({ mode: "edit", integrationId: connection.integration_id, connectionId: id })}>
                      Edit settings
                    </button>
                    {definition && definition.fields.some((f) => f.kind === "secret") ? (
                      <button type="button" className="button button--ghost button--small" disabled={busy}
                              onClick={() => setEditor({ mode: "credentials", integrationId: connection.integration_id, connectionId: id })}>
                        Replace credentials
                      </button>
                    ) : null}
                    {confirmDelete === id ? (
                      <>
                        <button type="button" className="button button--danger button--small" disabled={busy}
                                onClick={() => void run(() => deleteIntegrationConnection(apiKey, id),
                                  "Connection deleted, including its stored credentials.").then(() => setConfirmDelete(null))}>
                          Confirm delete
                        </button>
                        <button type="button" className="button button--ghost button--small" disabled={busy}
                                onClick={() => setConfirmDelete(null)}>
                          Keep
                        </button>
                      </>
                    ) : (
                      <button type="button" className="button button--danger button--small" disabled={busy}
                              onClick={() => setConfirmDelete(id)}>
                        Delete
                      </button>
                    )}
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
