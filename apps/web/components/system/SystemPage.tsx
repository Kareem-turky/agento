"use client";

// System: the read-only operational status of this Agento installation (Task 039), over
// the Product-authenticated System Status API (system.read). It shows fixed states and
// stable codes only: never a database URL, host, telemetry endpoint, identifier, path,
// configuration value or secret (the API never returns one). Nothing here restarts,
// migrates, backs up or restores anything; there is no polling (Refresh is explicit).
import { useCallback, useEffect, useState } from "react";
import { getSystemStatus } from "../../lib/product-api/client";
import type { ProductErrorKind, SystemComponentState, SystemStatusResponse } from "../../lib/product-api/types";
import { ConnectNotice } from "../shell/ConnectNotice";
import { PageHeader } from "../shell/PageHeader";
import { useProductSession } from "../shell/ProductSessionProvider";
import { Badge, Card, KeyValue, type Tone } from "../ui/primitives";

const STATE: Record<SystemComponentState, [string, Tone]> = {
  ready: ["Ready", "success"],
  starting: ["Starting", "pending"],
  unavailable: ["Unavailable", "danger"],
  mismatch: ["Schema mismatch", "danger"],
};

const REASON: Record<string, string> = {
  application_starting: "The application is still starting.",
  agent_runtime_starting: "The Agent runtime is not attached yet.",
  database_unavailable: "PostgreSQL is not reachable.",
  schema_mismatch: "The Product database schema is not at the revision this build expects. Run the Product migration.",
  schema_unavailable: "The Product database schema could not be read.",
};

const EXPORT: Record<string, string> = { disabled: "Disabled", otlp_http: "OTLP over HTTP (enabled)" };

function failureText(kind: ProductErrorKind): string {
  switch (kind) {
    case "unauthenticated":
      return "Product API key not accepted.";
    case "forbidden":
      return "You don't have access to System status.";
    case "service_unavailable":
      return "System status is unavailable right now.";
    default:
      return "System status could not be loaded.";
  }
}

function uptime(seconds: number): string {
  const days = Math.floor(seconds / 86400);
  const hours = Math.floor((seconds % 86400) / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  if (days > 0) return `${days} d ${hours} h`;
  if (hours > 0) return `${hours} h ${minutes} min`;
  return `${minutes} min`;
}

function State({ value }: { value: SystemComponentState }) {
  const [label, tone] = STATE[value] ?? [value, "neutral"];
  return <Badge tone={tone}>{label}</Badge>;
}

export function SystemPage() {
  // The key comes from the one ProductSessionProvider (memory only). Keyed by the session
  // epoch: a new key or a disconnect remounts it with nothing left over.
  const { apiKey, sessionEpoch } = useProductSession();
  const [refresh, setRefresh] = useState(0);

  return (
    <>
      <PageHeader
        title="System"
        description="The operational status of this Agento installation (read-only)."
        actions={apiKey !== null ? (
          <button type="button" className="button button--ghost" onClick={() => setRefresh((value) => value + 1)}>
            Refresh status
          </button>
        ) : null}
      />
      <div className="page-layout">
        <div className="page-layout__main" key={sessionEpoch}>
          {apiKey === null ? <ConnectNotice area="system status" /> : <SystemWorkspace apiKey={apiKey} refresh={refresh} />}
        </div>
        <aside className="page-layout__side">
          <Card title="Access">
            <p className="form__context">Viewing needs system.read. Nothing here changes the installation.</p>
          </Card>
          <Card title="Operations">
            <p className="form__context">
              Restarts, migrations, backups and restores are operator actions with the deployment tooling, never
              Product actions. Container health uses the minimal public readiness check.
            </p>
          </Card>
        </aside>
      </div>
    </>
  );
}

type Read = { state: "loading" } | { state: "ok"; data: SystemStatusResponse } | { state: "failed"; error: ProductErrorKind };

function SystemWorkspace({ apiKey, refresh }: { apiKey: string; refresh: number }) {
  const [read, setRead] = useState<Read>({ state: "loading" });

  const load = useCallback(() => getSystemStatus(apiKey), [apiKey]);

  useEffect(() => {
    let current = true;
    setRead({ state: "loading" });
    void load().then((result) => {
      if (!current) return; // a newer refresh or an unmount superseded this read
      setRead(result.ok ? { state: "ok", data: result.data } : { state: "failed", error: result.error });
    });
    return () => {
      current = false;
    };
  }, [load, refresh]);

  if (read.state === "loading") return <p className="loading">Loading…</p>;
  if (read.state === "failed") {
    return <p className="notice notice--attention" role="status">{failureText(read.error)}</p>;
  }
  const status = read.data;
  const ready = status.overall === "ready";
  return (
    <>
      <Card title="Readiness" subtitle="Whether this instance can serve Product work right now.">
        <div className="status-list">
          <div className="status-list__row">
            <span>Overall</span>
            <Badge tone={ready ? "success" : "danger"}>{ready ? "Ready" : "Not ready"}</Badge>
          </div>
        </div>
        {status.reasons.length > 0 ? (
          <ul className="overview-list" aria-label="Why the instance is not ready">
            {status.reasons.map((reason) => (
              <li key={reason}>{REASON[reason] ?? "An unrecognized readiness condition was reported."}</li>
            ))}
          </ul>
        ) : (
          <p className="empty">Every readiness component is ready.</p>
        )}
      </Card>
      <Card title="Components">
        <div className="status-list">
          <div className="status-list__row"><span>Application</span><State value={status.components.application} /></div>
          <div className="status-list__row"><span>Database</span><State value={status.components.database} /></div>
          <div className="status-list__row"><span>Product schema</span><State value={status.components.product_schema} /></div>
          <div className="status-list__row"><span>Agent runtime</span><State value={status.components.agent_runtime} /></div>
        </div>
      </Card>
      <Card title="Installation">
        <KeyValue items={[
          ["Product version", status.application.version],
          ["Environment", status.application.environment],
          ["Uptime", uptime(status.application.uptime_seconds)],
          ["Telemetry export", EXPORT[status.observability.export_mode] ?? status.observability.export_mode],
        ]} />
      </Card>
    </>
  );
}
