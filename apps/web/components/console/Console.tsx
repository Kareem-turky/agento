"use client";

import Link from "next/link";
import { useCallback, useEffect, useReducer, useRef, useState, type KeyboardEvent } from "react";
import { getHealth } from "../../lib/product-api/client";
import type { ProductResult } from "../../lib/product-api/types";
import { AnalysisPanel } from "./AnalysisPanel";
import { CommandPanel } from "./CommandPanel";
import { ReportPanel } from "./ReportPanel";
import { SessionPanel, type HealthState } from "./SessionPanel";
import { initialSession, sessionReducer } from "./session";
import { TicketPanel } from "./TicketPanel";

const TABS = [
  { id: "analysis", label: "Analyze operations" },
  { id: "report", label: "Daily report" },
  { id: "ticket", label: "Operational ticket" },
  { id: "command", label: "Command status" },
] as const;

type TabId = (typeof TABS)[number]["id"];

export function Console() {
  // The Product API key lives ONLY in this component's memory for the page lifetime.
  const [apiKey, setApiKey] = useState<string | null>(null);
  // Session context (integer epoch, key status, store, recent command); never the key.
  const [session, dispatch] = useReducer(sessionReducer, initialSession);
  const { epoch, keyStatus, storeId, recentCommandId } = session;
  const [health, setHealth] = useState<HealthState>("checking");
  const [tab, setTab] = useState<TabId>("analysis");
  const tabRefs = useRef<(HTMLButtonElement | null)[]>([]);

  const checkHealth = useCallback(async () => {
    setHealth("checking");
    const result = await getHealth();
    // Reachability only: it says nothing about whether a key is accepted.
    setHealth(result.ok && result.data.status === "ok" ? "reachable" : "unavailable");
  }, []);

  useEffect(() => {
    void checkHealth();
  }, [checkHealth]);

  // Panel completions carry the epoch they were started in; the reducer ignores any
  // that belong to an older session context.
  const onAuthResult = useCallback((sourceEpoch: number, result: ProductResult<unknown>) => {
    const outcome = result.ok ? "accepted" : result.error === "unauthenticated" ? "rejected" : "other";
    dispatch({ type: "authResult", epoch: sourceEpoch, outcome });
  }, []);

  const onCommand = useCallback((sourceEpoch: number, commandId: string) => {
    dispatch({ type: "commandCreated", epoch: sourceEpoch, commandId });
  }, []);

  function applyKey(key: string) {
    setApiKey(key);
    dispatch({ type: "keySet" });
  }

  function changeStore(value: string) {
    dispatch({ type: "storeChanged", storeId: value });
  }

  function disconnect() {
    setApiKey(null);
    dispatch({ type: "disconnected" });
  }

  function onTabKey(event: KeyboardEvent<HTMLButtonElement>, index: number) {
    const step = event.key === "ArrowRight" ? 1 : event.key === "ArrowLeft" ? -1 : 0;
    if (!step) return;
    event.preventDefault();
    const next = (index + step + TABS.length) % TABS.length;
    setTab(TABS[next].id);
    tabRefs.current[next]?.focus();
  }

  return (
    <div className="shell">
      <header className="topbar">
        <div className="topbar__brand">
          <span className="topbar__mark" aria-hidden="true">◆</span>
          <div>
            <p className="topbar__title">Operations Console</p>
            <p className="topbar__subtitle">Store operations over the Product API</p>
          </div>
        </div>
        <nav className="topbar__nav" aria-label="Pages">
          <Link href="/settings/integrations">Integrations</Link>
        </nav>
        <span className={`pill pill--${health}`} role="status">
          {health === "checking" ? "Checking API…" : health === "reachable" ? "API reachable" : "API unavailable"}
        </span>
      </header>

      <div className="layout">
        <aside className="layout__side">
          <SessionPanel
            keyStatus={keyStatus}
            storeId={storeId}
            health={health}
            onUseKey={applyKey}
            onStoreChange={changeStore}
            onCheckHealth={() => void checkHealth()}
            onDisconnect={disconnect}
          />
        </aside>

        {/* Keyed by the session epoch: a new key, a new store or a disconnect remounts
            every panel, so no result, form or pending ticket key of an older context
            stays visible. */}
        <main className="layout__main" key={epoch}>
          <div className="tabs" role="tablist" aria-label="Console sections">
            {TABS.map((item, index) => (
              <button
                key={item.id}
                ref={(element) => { tabRefs.current[index] = element; }}
                type="button"
                role="tab"
                id={`tab-${item.id}`}
                aria-selected={tab === item.id}
                aria-controls={`panel-${item.id}`}
                tabIndex={tab === item.id ? 0 : -1}
                className="tabs__tab"
                onClick={() => setTab(item.id)}
                onKeyDown={(event) => onTabKey(event, index)}
              >
                {item.label}
              </button>
            ))}
          </div>

          {/* Panels stay mounted (hidden) so switching tabs keeps their state. */}
          <div role="tabpanel" id="panel-analysis" aria-labelledby="tab-analysis" hidden={tab !== "analysis"}>
            <AnalysisPanel epoch={epoch} apiKey={apiKey} storeId={storeId} onAuthResult={onAuthResult} />
          </div>
          <div role="tabpanel" id="panel-report" aria-labelledby="tab-report" hidden={tab !== "report"}>
            <ReportPanel epoch={epoch} apiKey={apiKey} storeId={storeId} onAuthResult={onAuthResult} />
          </div>
          <div role="tabpanel" id="panel-ticket" aria-labelledby="tab-ticket" hidden={tab !== "ticket"}>
            <TicketPanel epoch={epoch} apiKey={apiKey} storeId={storeId} onAuthResult={onAuthResult}
                         onCommand={onCommand} />
          </div>
          <div role="tabpanel" id="panel-command" aria-labelledby="tab-command" hidden={tab !== "command"}>
            <CommandPanel epoch={epoch} apiKey={apiKey} recentCommandId={recentCommandId}
                          onAuthResult={onAuthResult} />
          </div>
        </main>
      </div>
    </div>
  );
}
