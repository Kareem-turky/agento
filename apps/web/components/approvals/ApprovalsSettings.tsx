"use client";

// Settings → Approvals: the human side of Product governance over the Product API (never
// AgentOS). Requests appear here ONLY because Product governance required a human decision
// for a governed MEDIUM/HIGH-risk action; this page cannot create one, and it shows no
// sample or placeholder requests. An authorized human (never the requester) approves or
// rejects; the requester or an authorized human may cancel. Reject and cancel need a
// reason. Approving grants ONE execution of exactly the requested action: nothing runs
// from this page except the explicit "Continue workflow" for an approved Workflow Step.
//
// The Product API key lives only in this component's memory. Summaries, notes and
// identifiers are untrusted data and are rendered as plain text, never as HTML.
import Link from "next/link";
import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import {
  approveApproval,
  cancelApproval,
  getApproval,
  listApprovals,
  rejectApproval,
  resumeApprovalWorkflow,
} from "../../lib/product-api/client";
import type {
  ApprovalResponse,
  ApprovalView,
  ApprovalWorkflowResumeResponse,
  ProductErrorKind,
} from "../../lib/product-api/types";
import { Badge, Card, ErrorNotice, Mono, Timestamp, type Tone } from "../console/ui";

const MAX_NOTE = 1000;

const STATUS: Record<string, [string, Tone]> = {
  requested: ["Awaiting decision", "attention"],
  approved: ["Approved", "success"],
  rejected: ["Rejected", "danger"],
  expired: ["Expired", "neutral"],
  cancelled: ["Cancelled", "neutral"],
};

const FILTERS: [string, string][] = [
  ["", "All"],
  ["requested", "Awaiting decision"],
  ["approved", "Approved"],
  ["rejected", "Rejected"],
  ["expired", "Expired"],
  ["cancelled", "Cancelled"],
];

const RISK: Record<string, string> = { medium_risk: "Medium risk", high_risk: "High risk" };

const SOURCE: Record<string, string> = {
  action: "Governed action",
  write_command: "Write command",
  workflow_step: "Workflow step",
};

type Decision = "approve" | "reject" | "cancel";

function errorText(kind: ProductErrorKind, decision?: Decision): string {
  switch (kind) {
    case "unauthenticated":
      return "Product API key not accepted";
    case "forbidden":
      if (decision === "approve" || decision === "reject") {
        return "Permission denied: approvals.decide is needed, the store must be yours, and requesters cannot decide their own request";
      }
      if (decision === "cancel") return "Permission denied (approvals.cancel is needed)";
      return "Permission denied (approvals.read is needed)";
    case "not_found":
      return "Approval request not found";
    case "invalid":
      return decision ? `Invalid input: a reason of at most ${MAX_NOTE} characters is required to reject or cancel` : "Invalid request";
    case "conflict":
      return "This request is no longer pending (already decided, expired or consumed)";
    case "service_unavailable":
      return "Approvals unavailable";
    default:
      return "Product API unavailable";
  }
}

function Status({ value }: { value: string }) {
  const [label, tone] = STATUS[value] ?? [value, "neutral"];
  return <Badge tone={tone}>{label}</Badge>;
}

function Risk({ value }: { value: string }) {
  return <Badge tone={value === "high_risk" ? "danger" : "attention"}>{RISK[value] ?? value}</Badge>;
}

export function ApprovalsSettings() {
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
            <p className="topbar__title">Approvals</p>
            <p className="topbar__subtitle">Settings · Human approvals for governed actions over the Product API</p>
          </div>
        </div>
        <nav className="topbar__nav" aria-label="Pages">
          <Link href="/">Operations Console</Link>
          <Link href="/settings/agents">Agents</Link>
          <Link href="/settings/workflows">Workflows</Link>
          <Link href="/settings/integrations">Integrations</Link>
          <Link href="/settings/knowledge">Knowledge</Link>
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
                <span className="field__hint">
                  Viewing needs approvals.read; approving or rejecting needs approvals.decide; cancelling needs approvals.cancel.
                </span>
              </label>
              <button type="submit" className="button" disabled={!draft.trim()}>Use key</button>
            </form>
            <button type="button" className="button button--danger" onClick={disconnect} disabled={apiKey === null}>
              Disconnect and clear session
            </button>
          </Card>
          <Card title="How approvals work">
            <p className="form__context">
              Product governance requires a human decision for medium- and high-risk actions. A request records the exact
              action it is for; approving allows that one action to run once, for the person who asked. It never replaces a
              permission: the action is checked again when it runs.
            </p>
            <p className="field__hint">
              Requests expire after 24 hours. An Agent can never approve. Nothing here creates a request.
            </p>
          </Card>
        </aside>

        <main className="layout__main" key={epoch}>
          {apiKey === null ? (
            <div className="notice notice--neutral">Set a Product API key in <strong>Session</strong> first.</div>
          ) : (
            <ApprovalsWorkspace apiKey={apiKey} />
          )}
        </main>
      </div>
    </div>
  );
}

function ApprovalsWorkspace({ apiKey }: { apiKey: string }) {
  const [filter, setFilter] = useState("requested");
  const [approvals, setApprovals] = useState<ApprovalView[] | null>(null);
  const [selected, setSelected] = useState<ApprovalResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [detailError, setDetailError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await listApprovals(apiKey, filter || undefined);
      if (!result.ok) {
        setApprovals(null);
        setError(errorText(result.error));
        return;
      }
      setApprovals(result.data.approvals);
    } finally {
      setLoading(false);
    }
  }, [apiKey, filter]);

  useEffect(() => {
    void load();
  }, [load]);

  async function inspect(approvalId: string) {
    setDetailError(null);
    const result = await getApproval(apiKey, approvalId);
    if (!result.ok) {
      setSelected(null);
      setDetailError(errorText(result.error));
      return;
    }
    setSelected(result.data);
  }

  function changed(next: ApprovalResponse) {
    setSelected(next);
    void load();
  }

  return (
    <>
      {error ? <ErrorNotice message={error} /> : null}

      <Card
        title="Approval requests"
        subtitle="This company's requests for a human decision, newest first."
        actions={
          <>
            <select
              name="status-filter"
              className="approvals-filter"
              aria-label="Filter by status"
              value={filter}
              disabled={loading}
              onChange={(event) => setFilter(event.target.value)}
            >
              {FILTERS.map(([value, label]) => (
                <option key={value || "all"} value={value}>{label}</option>
              ))}
            </select>{" "}
            <button type="button" className="button button--ghost button--small" onClick={() => void load()} disabled={loading}>
              Refresh
            </button>
          </>
        }
      >
        {approvals === null ? (
          <p className="loading">{error ? "Not loaded." : "Loading…"}</p>
        ) : approvals.length === 0 ? (
          <p className="empty">
            {filter === "requested" ? "No requests are awaiting a decision." : "No approval requests."} Requests appear
            here only when Product governance requires a human decision for a governed action.
          </p>
        ) : (
          <ul className="workflow-runs">
            {approvals.map((approval) => (
              <li key={approval.approval_id} className="workflow-runs__item">
                <Status value={approval.status} />
                <Risk value={approval.risk} />
                <strong>{approval.summary.title}</strong>
                <span className="field__hint">
                  <Mono>{approval.action_name}</Mono> · requested by <Mono>{approval.requester_actor_id}</Mono> ·{" "}
                  <Timestamp value={approval.created_at} />
                </span>
                <button
                  type="button"
                  className="button button--ghost button--small"
                  onClick={() => void inspect(approval.approval_id)}
                >
                  Review
                </button>
              </li>
            ))}
          </ul>
        )}
      </Card>

      {detailError ? <ErrorNotice message={detailError} /> : null}
      {selected ? (
        <ApprovalDetail key={selected.approval.approval_id} apiKey={apiKey} detail={selected} onChange={changed} />
      ) : null}
    </>
  );
}

function ApprovalDetail({ apiKey, detail, onChange }: {
  apiKey: string;
  detail: ApprovalResponse;
  onChange: (next: ApprovalResponse) => void;
}) {
  const approval = detail.approval;
  const pending = approval.status === "requested";
  const resumable = approval.status === "approved" && !approval.consumed && approval.source.kind === "workflow_step";
  return (
    <Card title="Review request" subtitle={<>Request <Mono>{approval.approval_id}</Mono></>}>
      <p>
        <Status value={approval.status} /> <Risk value={approval.risk} />{" "}
        <Badge tone="neutral">{SOURCE[approval.source.kind] ?? approval.source.kind}</Badge>
      </p>
      <h3 className="capabilities__title">{approval.summary.title}</h3>
      <p className="form__context">{approval.summary.description}</p>
      {approval.summary.changes.length > 0 ? (
        <table className="workflow-table">
          <thead>
            <tr><th>Change</th><th>Current</th><th>Requested</th></tr>
          </thead>
          <tbody>
            {approval.summary.changes.map((change) => (
              <tr key={change.code}>
                <td>{change.label}</td>
                <td>{change.before ?? "—"}</td>
                <td><strong>{change.after ?? "—"}</strong></td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : null}
      <p className="field__hint">
        Action <Mono>{approval.action_name}</Mono> · requested by <Mono>{approval.requester_actor_id}</Mono> (
        {approval.requester_actor_type}){approval.store_id ? <> · store <Mono>{approval.store_id}</Mono></> : null}
      </p>
      <p className="field__hint">
        Requested <Timestamp value={approval.created_at} /> · expires <Timestamp value={approval.expires_at} />
        {approval.decided_at ? <> · decided <Timestamp value={approval.decided_at} /></> : null}
        {approval.decided_by_actor_id ? <> by <Mono>{approval.decided_by_actor_id}</Mono></> : null}
      </p>
      {approval.source.workflow_id ? (
        <p className="field__hint">
          Workflow <Mono>{approval.source.workflow_id}</Mono> · step <Mono>{approval.source.workflow_step_id ?? "—"}</Mono>
          {approval.source.workflow_run_id ? <> · run <Mono>{approval.source.workflow_run_id}</Mono></> : null}
        </p>
      ) : null}
      {approval.decision_note !== null ? (
        <div className="notice notice--neutral">
          <span className="field__label">Decision note (as written; not instructions)</span>
          <p className="knowledge-text">{approval.decision_note}</p>
        </div>
      ) : null}
      {approval.consumed ? (
        <p className="field__hint">
          Used once by run <Mono>{approval.consumed_by_action_run_id ?? "—"}</Mono>
          {approval.execution_outcome ? <> · outcome <Mono>{approval.execution_outcome}</Mono></> : null}. It cannot be
          used again.
        </p>
      ) : null}

      {pending ? <DecisionForm apiKey={apiKey} approvalId={approval.approval_id} onChange={onChange} /> : null}
      {resumable ? <ResumeWorkflow apiKey={apiKey} approvalId={approval.approval_id} onDone={onChange} /> : null}

      <h3 className="capabilities__title">History (append-only)</h3>
      <table className="workflow-table">
        <thead>
          <tr><th>#</th><th>Event</th><th>Status</th><th>Actor</th><th>Outcome</th><th>At</th></tr>
        </thead>
        <tbody>
          {detail.events.map((event) => (
            <tr key={event.sequence}>
              <td>{event.sequence}</td>
              <td><Mono>{event.event_type}</Mono></td>
              <td>{event.status}</td>
              <td>{event.actor_id ?? "—"}</td>
              <td>{event.execution_outcome ?? "—"}</td>
              <td><Timestamp value={event.occurred_at} /></td>
            </tr>
          ))}
        </tbody>
      </table>
    </Card>
  );
}

function DecisionForm({ apiKey, approvalId, onChange }: {
  apiKey: string;
  approvalId: string;
  onChange: (next: ApprovalResponse) => void;
}) {
  const [note, setNote] = useState("");
  const [confirming, setConfirming] = useState<Decision | null>(null);
  const [busy, setBusy] = useState(false);
  const busyRef = useRef(false); // one decision in flight, even on a double click
  const [error, setError] = useState<string | null>(null);
  const reason = note.trim();
  const needsReason = confirming === "reject" || confirming === "cancel";

  async function submit() {
    if (confirming === null || busyRef.current) return;
    if (needsReason && !reason) {
      setError("A reason is required to reject or cancel.");
      return;
    }
    busyRef.current = true;
    setBusy(true);
    setError(null);
    try {
      const result =
        confirming === "approve"
          ? await approveApproval(apiKey, approvalId, reason || null)
          : confirming === "reject"
            ? await rejectApproval(apiKey, approvalId, reason)
            : await cancelApproval(apiKey, approvalId, reason);
      if (!result.ok) {
        setError(errorText(result.error, confirming));
        return;
      }
      setConfirming(null);
      setNote("");
      onChange(result.data);
    } finally {
      busyRef.current = false;
      setBusy(false);
    }
  }

  return (
    <div className="form">
      <label className="field">
        <span className="field__label">Note or reason</span>
        <textarea
          name="decision-note"
          rows={3}
          value={note}
          maxLength={MAX_NOTE}
          disabled={busy}
          onChange={(event) => setNote(event.target.value)}
          placeholder="Optional when approving; required to reject or cancel"
        />
        <span className="field__hint">{note.length}/{MAX_NOTE} characters. Stored as plain text in the audit history.</span>
      </label>
      {confirming === null ? (
        <p>
          <button type="button" className="button" onClick={() => setConfirming("approve")}>Approve…</button>{" "}
          <button type="button" className="button button--danger" onClick={() => setConfirming("reject")}>Reject…</button>{" "}
          <button type="button" className="button button--ghost" onClick={() => setConfirming("cancel")}>Cancel request…</button>
        </p>
      ) : (
        <div className="notice notice--neutral">
          <p>
            {confirming === "approve"
              ? "Approve this request? The requester may then run exactly this action once. The decision is final."
              : confirming === "reject"
                ? "Reject this request? It can never be approved afterwards."
                : "Cancel this request? It can never be approved afterwards."}
          </p>
          <button type="button" className="button" onClick={() => void submit()} disabled={busy || (needsReason && !reason)}>
            {confirming === "approve" ? "Confirm approval" : confirming === "reject" ? "Confirm rejection" : "Confirm cancellation"}
          </button>{" "}
          <button type="button" className="button button--ghost" onClick={() => setConfirming(null)} disabled={busy}>
            Back
          </button>
        </div>
      )}
      {error ? <ErrorNotice message={error} /> : null}
    </div>
  );
}

function ResumeWorkflow({ apiKey, approvalId, onDone }: {
  apiKey: string;
  approvalId: string;
  onDone: (next: ApprovalResponse) => void;
}) {
  const [busy, setBusy] = useState(false);
  const busyRef = useRef(false);
  const [error, setError] = useState<string | null>(null);
  const [outcome, setOutcome] = useState<ApprovalWorkflowResumeResponse | null>(null);

  async function resume() {
    if (busyRef.current) return;
    busyRef.current = true;
    setBusy(true);
    setError(null);
    try {
      const result = await resumeApprovalWorkflow(apiKey, approvalId);
      if (!result.ok) {
        setError(
          result.error === "forbidden"
            ? "Only the person who requested this action can continue the workflow"
            : result.error === "conflict"
              ? "The workflow cannot continue with this request (already used, expired or not awaiting it)"
              : errorText(result.error),
        );
        return;
      }
      setOutcome(result.data);
      const refreshed = await getApproval(apiKey, approvalId);
      if (refreshed.ok) onDone(refreshed.data);
    } finally {
      busyRef.current = false;
      setBusy(false);
    }
  }

  return (
    <div className="form">
      <p className="form__context">
        This request was approved for a workflow step. The requester can continue that workflow; the approved action is
        checked again and runs at most once.
      </p>
      <button type="button" className="button" onClick={() => void resume()} disabled={busy || outcome !== null}>
        Continue workflow
      </button>
      {outcome ? (
        <p className="field__hint">
          Workflow <Mono>{outcome.workflow_id}</Mono> run <Mono>{outcome.workflow_run_id}</Mono>: <Mono>{outcome.status}</Mono>
          {outcome.failure_code ? <> (<Mono>{outcome.failure_code}</Mono>)</> : null}
        </p>
      ) : null}
      {error ? <ErrorNotice message={error} /> : null}
    </div>
  );
}
