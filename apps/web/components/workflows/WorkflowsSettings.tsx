"use client";

// Settings → Workflows: a READ-ONLY view of the Product Workflow Platform over the Product
// API (never AgentOS). It shows the Workflows installed in this build (ordered Steps,
// side-effect class, timeouts, bounded attempts) and this company's recent Workflow runs
// with their Step attempts, safe failure codes and lifecycle events. There is no Run,
// Retry or Resume button, no input form and no Workflow, JSON or code editor: Workflows are
// reviewed Product source code and run only inside trusted Product services.
//
// The Product API key lives only in this component's memory; every value is rendered as
// plain text.
import Link from "next/link";
import { useCallback, useEffect, useState, type FormEvent } from "react";
import { getWorkflowCatalog, getWorkflowRun, listWorkflowRuns } from "../../lib/product-api/client";
import type {
  ProductErrorKind,
  WorkflowDefinitionView,
  WorkflowRunResponse,
  WorkflowRunView,
} from "../../lib/product-api/types";
import { Badge, Card, ErrorNotice, Mono, Timestamp, type Tone } from "../console/ui";

const RUN_STATUS: Record<string, [string, Tone]> = {
  pending: ["Pending", "pending"],
  running: ["Running", "pending"],
  succeeded: ["Succeeded", "success"],
  failed: ["Failed", "danger"],
  requires_human: ["Requires a human", "attention"],
  awaiting_approval: ["Awaiting approval", "attention"],
  timed_out: ["Timed out", "danger"],
};

/** Fixed, safe explanations of the Product's stable failure codes. */
const FAILURE: Record<string, string> = {
  input_invalid: "The Workflow input was invalid.",
  handler_not_registered: "A Step is not available in this deployment.",
  access_denied: "Denied by Product governance; nothing was changed.",
  step_timeout: "A Step exceeded its timeout.",
  step_execution_failed: "A Step failed (no effect).",
  step_verification_failed: "A Step's result could not be verified.",
  step_outcome_uncertain: "A write's outcome is uncertain; a person must check it.",
  checkpoint_invalid: "A Step's checkpoint was invalid.",
  retry_exhausted: "Every allowed attempt failed.",
  executor_lost: "The executing process was lost.",
  approval_required: "Stopped: the action requires approval.",
  workflow_unavailable: "The Workflow was unavailable.",
  lease_conflict: "Another executor held the run.",
};

function errorText(kind: ProductErrorKind): string {
  switch (kind) {
    case "unauthenticated":
      return "Product API key not accepted";
    case "forbidden":
      return "Permission denied (workflows.read is needed)";
    case "not_found":
      return "Workflow run not found";
    case "invalid":
      return "Invalid request";
    case "service_unavailable":
      return "Workflows unavailable";
    default:
      return "Product API unavailable";
  }
}

function Status({ value }: { value: string }) {
  const [label, tone] = RUN_STATUS[value] ?? [value, "neutral"];
  return <Badge tone={tone}>{label}</Badge>;
}

function Failure({ code }: { code: string | null }) {
  if (!code) return null;
  return (
    <span className="field__hint">
      {" "}<Mono>{code}</Mono> {FAILURE[code] ?? ""}
    </span>
  );
}

export function WorkflowsSettings() {
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
            <p className="topbar__title">Workflows</p>
            <p className="topbar__subtitle">Settings · Product Workflows over the Product API (read-only)</p>
          </div>
        </div>
        <nav className="topbar__nav" aria-label="Pages">
          <Link href="/">Operations Console</Link>
          <Link href="/settings/agents">Agents</Link>
          <Link href="/settings/integrations">Integrations</Link>
          <Link href="/settings/knowledge">Knowledge</Link>
          <Link href="/settings/approvals">Approvals</Link>
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
                <span className="field__hint">Viewing needs workflows.read. Nothing here runs a Workflow.</span>
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
            <WorkflowsWorkspace apiKey={apiKey} />
          )}
        </main>
      </div>
    </div>
  );
}

function WorkflowsWorkspace({ apiKey }: { apiKey: string }) {
  const [workflows, setWorkflows] = useState<WorkflowDefinitionView[] | null>(null);
  const [runs, setRuns] = useState<WorkflowRunView[] | null>(null);
  const [selected, setSelected] = useState<WorkflowRunResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [detailError, setDetailError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [catalog, history] = await Promise.all([getWorkflowCatalog(apiKey), listWorkflowRuns(apiKey)]);
      if (!catalog.ok) {
        setError(errorText(catalog.error));
        return;
      }
      setWorkflows(catalog.data.workflows);
      if (!history.ok) {
        setError(errorText(history.error));
        return;
      }
      setRuns(history.data.runs);
    } finally {
      setLoading(false);
    }
  }, [apiKey]);

  useEffect(() => {
    void load();
  }, [load]);

  async function inspect(runId: string) {
    setDetailError(null);
    const result = await getWorkflowRun(apiKey, runId);
    if (!result.ok) {
      setSelected(null);
      setDetailError(errorText(result.error));
      return;
    }
    setSelected(result.data);
  }

  return (
    <>
      {error ? <ErrorNotice message={error} /> : null}

      <Card
        title="Product Workflows"
        subtitle="Deterministic Product processes installed in this build. Steps run strictly in this order; their behaviour is reviewed Product code."
        actions={<button type="button" className="button button--ghost button--small" onClick={() => void load()}
                         disabled={loading}>Refresh</button>}
      >
        {workflows === null ? (
          <p className="loading">{error ? "Not loaded." : "Loading…"}</p>
        ) : workflows.length === 0 ? (
          <p className="empty">No Product Workflows are installed in this build.</p>
        ) : (
          <ul className="integration-list">
            {workflows.map((workflow) => (
              <li key={workflow.workflow_id} className="integration-list__item">
                <div className="integration-list__body">
                  <p className="integration-list__title">
                    <strong>{workflow.name}</strong> <Mono>{workflow.workflow_id}</Mono>{" "}
                    <Badge tone="neutral">v{workflow.version}</Badge>
                  </p>
                  <p className="field__hint">{workflow.category} · {workflow.lifecycle}</p>
                  <p className="form__context">{workflow.description}</p>
                  {workflow.inputs.length > 0 ? (
                    <p className="field__hint">
                      Inputs: {workflow.inputs.map((f) => `${f.label} (${f.kind}${f.required ? "" : ", optional"})`).join(", ")}
                    </p>
                  ) : null}
                  <ol className="workflow-steps">
                    {workflow.steps.map((step) => (
                      <li key={step.step_id}>
                        <strong>{step.name}</strong> <Mono>{step.step_id}</Mono>{" "}
                        <Badge tone={step.side_effect === "governed_write" ? "attention" : "neutral"}>
                          {step.side_effect === "governed_write" ? "governed write" : "read-only"}
                        </Badge>
                        <span className="field__hint"> {step.description}</span>
                        <span className="field__hint capabilities__line">
                          Handler <Mono>{step.handler_id}</Mono> · timeout {step.timeout_seconds}s · at most{" "}
                          {step.max_attempts} attempt{step.max_attempts === 1 ? "" : "s"} · checkpoint {step.checkpoint_policy}
                        </span>
                      </li>
                    ))}
                  </ol>
                </div>
              </li>
            ))}
          </ul>
        )}
      </Card>

      <Card title="Recent runs" subtitle="This company's most recent Workflow runs (newest first). Inputs, checkpoints and results are never shown or stored here.">
        {runs === null ? (
          <p className="loading">{error ? "Not loaded." : "Loading…"}</p>
        ) : runs.length === 0 ? (
          <p className="empty">No Workflow runs yet.</p>
        ) : (
          <ul className="workflow-runs">
            {runs.map((run) => (
              <li key={run.run_id} className="workflow-runs__item">
                <Status value={run.status} />
                <Mono>{run.workflow_id}</Mono>
                <span className="field__hint">
                  <Timestamp value={run.created_at} /> · {run.attempt_count} attempt{run.attempt_count === 1 ? "" : "s"}
                  {run.current_step_id ? <> · step <Mono>{run.current_step_id}</Mono></> : null}
                </span>
                <Failure code={run.failure_code} />
                <button type="button" className="button button--ghost button--small" onClick={() => void inspect(run.run_id)}>
                  Details
                </button>
              </li>
            ))}
          </ul>
        )}
      </Card>

      {detailError ? <ErrorNotice message={detailError} /> : null}
      {selected ? <RunDetail detail={selected} /> : null}
    </>
  );
}

function RunDetail({ detail }: { detail: WorkflowRunResponse }) {
  const run = detail.run;
  return (
    <Card title="Run details" subtitle={<>Run <Mono>{run.run_id}</Mono> · request <Mono>{run.request_id}</Mono></>}>
      <p>
        <Status value={run.status} /> <Mono>{run.workflow_id}</Mono> v{run.workflow_version}
        <Failure code={run.failure_code} />
      </p>
      <p className="field__hint">
        Started <Timestamp value={run.created_at} />
        {run.completed_at ? <> · completed <Timestamp value={run.completed_at} /></> : null}
      </p>
      <h3 className="capabilities__title">Step attempts</h3>
      <table className="workflow-table">
        <thead>
          <tr><th>Step</th><th>Attempt</th><th>Status</th><th>Failure</th><th>Verification</th><th>Started</th><th>Completed</th></tr>
        </thead>
        <tbody>
          {detail.attempts.map((attempt) => (
            <tr key={`${attempt.step_id}-${attempt.attempt}`}>
              <td><Mono>{attempt.step_id}</Mono></td>
              <td>{attempt.attempt}</td>
              <td><Status value={attempt.status} /></td>
              <td>{attempt.failure_code ? <Mono>{attempt.failure_code}</Mono> : "—"}</td>
              <td>{attempt.verification_code ?? "—"}</td>
              <td><Timestamp value={attempt.started_at} /></td>
              <td>{attempt.completed_at ? <Timestamp value={attempt.completed_at} /> : "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <h3 className="capabilities__title">Lifecycle events (append-only)</h3>
      <table className="workflow-table">
        <thead>
          <tr><th>#</th><th>Event</th><th>Step</th><th>Attempt</th><th>Status</th><th>Failure</th><th>At</th></tr>
        </thead>
        <tbody>
          {detail.events.map((event) => (
            <tr key={event.sequence}>
              <td>{event.sequence}</td>
              <td><Mono>{event.event_type}</Mono></td>
              <td>{event.step_id ?? "—"}</td>
              <td>{event.attempt ?? "—"}</td>
              <td>{event.status ?? "—"}</td>
              <td>{event.failure_code ?? "—"}</td>
              <td><Timestamp value={event.occurred_at} /></td>
            </tr>
          ))}
        </tbody>
      </table>
    </Card>
  );
}
