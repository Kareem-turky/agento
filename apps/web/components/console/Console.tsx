"use client";

// Operations: the existing Operations Console (read-only analysis, daily report, explicit
// operational tickets and command status), inside the Agento shell. The Product API key
// and the Store UUID come from the ONE ProductSessionProvider; nothing here runs on its
// own: every request is an explicit submit.
import { useCallback, useEffect, useRef, useState, type KeyboardEvent, type RefObject } from "react";
import { looksLikeUuid } from "../../lib/product-api/client";
import type { ProductResult } from "../../lib/product-api/types";
import { PageHeader } from "../shell/PageHeader";
import { useProductSession } from "../shell/ProductSessionProvider";
import { Badge, Card } from "../ui/primitives";
import { AnalysisPanel } from "./AnalysisPanel";
import { CommandPanel } from "./CommandPanel";
import { ReportPanel } from "./ReportPanel";
import { TABS, type TabId } from "./tabs";
import { TicketPanel } from "./TicketPanel";

export function Console({ initialTab }: { initialTab: TabId }) {
  const session = useProductSession();
  const { apiKey, sessionEpoch, operationsEpoch, storeId, recentCommandId } = session;
  const [tab, setTab] = useState<TabId>(initialTab);
  const tabRefs = useRef<(HTMLButtonElement | null)[]>([]);

  // A deep link (for example from an Overview quick action) selects its tab.
  useEffect(() => {
    setTab(initialTab);
  }, [initialTab]);

  function select(next: TabId) {
    setTab(next);
  }

  function onTabKey(event: KeyboardEvent<HTMLButtonElement>, index: number) {
    const step = event.key === "ArrowRight" ? 1 : event.key === "ArrowLeft" ? -1 : 0;
    if (!step) return;
    event.preventDefault();
    const next = (index + step + TABS.length) % TABS.length;
    select(TABS[next].id);
    tabRefs.current[next]?.focus();
  }

  return (
    <>
      <PageHeader
        title="Operations"
        description="Read-only analysis, the daily operations report and explicit operational tickets for one store."
      />
      <div className="page-layout page-layout--context-first">
        {/* Keyed by the operations epoch: a new key, a new store or a disconnect remounts
            every panel, so no result, form or pending ticket key of an older context
            stays visible. */}
        <div className="page-layout__main" key={operationsEpoch}>
          <OperationsPanels
            sessionEpoch={sessionEpoch}
            operationsEpoch={operationsEpoch}
            apiKey={apiKey}
            storeId={storeId}
            recentCommandId={recentCommandId}
            tab={tab}
            select={select}
            onTabKey={onTabKey}
            tabRefs={tabRefs}
          />
        </div>
        <aside className="page-layout__side">
          <StoreCard />
        </aside>
      </div>
    </>
  );
}

function OperationsPanels({ sessionEpoch, operationsEpoch, apiKey, storeId, recentCommandId, tab, select, onTabKey, tabRefs }: {
  sessionEpoch: number;
  operationsEpoch: number;
  apiKey: string | null;
  storeId: string;
  recentCommandId: string | null;
  tab: TabId;
  select: (tab: TabId) => void;
  onTabKey: (event: KeyboardEvent<HTMLButtonElement>, index: number) => void;
  tabRefs: RefObject<(HTMLButtonElement | null)[]>;
}) {
  const { reportResult, commandCreated } = useProductSession();

  // Panel completions carry the epoch they were started in; the session ignores any that
  // belong to an older context. This instance lives within one session epoch.
  const onAuthResult = useCallback(
    (_epoch: number, result: ProductResult<unknown>) => reportResult(sessionEpoch, result),
    [reportResult, sessionEpoch],
  );
  const onCommand = useCallback(
    (sourceEpoch: number, commandId: string) => commandCreated(sourceEpoch, commandId),
    [commandCreated],
  );
  const epoch = operationsEpoch;

  return (
    <>
      <div className="tabs" role="tablist" aria-label="Operations sections">
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
            onClick={() => select(item.id)}
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
    </>
  );
}

/** The store is request context for Operations, never authorization. */
function StoreCard() {
  const { storeId, changeStore } = useProductSession();
  const storeValid = storeId === "" || looksLikeUuid(storeId);
  return (
    <Card title="Store" subtitle="Kept for this session while you move between pages.">
      <div className="status-list">
        <div className="status-list__row">
          <span>Store UUID</span>
          {storeId && storeValid ? <Badge tone="success">Set</Badge>
            : <Badge tone="neutral">{storeId ? "Not a UUID" : "Not set"}</Badge>}
        </div>
      </div>
      <div className="field">
        <label className="field__label" htmlFor="store-id">Store UUID</label>
        <input
          id="store-id"
          name="store-id"
          inputMode="text"
          spellCheck={false}
          autoComplete="off"
          value={storeId}
          onChange={(event) => changeStore(event.target.value.trim())}
          placeholder="00000000-0000-4000-8000-000000000000"
          aria-invalid={!storeValid}
          aria-describedby="store-id-hint"
        />
        <span className="field__hint" id="store-id-hint">
          {storeValid ? "The Product API decides whether this key may access the store. Changing it clears Operations results."
            : "Enter a UUID (format check only)."}
        </span>
      </div>
    </Card>
  );
}
