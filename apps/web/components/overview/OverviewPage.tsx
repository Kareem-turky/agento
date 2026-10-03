"use client";

// Overview: the Agento Product Control Center. It is a READ-ONLY dashboard over the
// EXISTING Product APIs: on connect (and on an explicit "Refresh overview") it issues
// independent, parallel GET reads, and each card handles its own failure. It never runs
// an Agent or a model, never loads a report, never creates a ticket, never decides an
// approval, never tests an integration and never changes anything. Quick actions only
// navigate to Operations. There is no polling.
//
// Every value is rendered as plain text: titles, names and identifiers are data, never
// markup. No message or document body is shown here.
import Link from "next/link";
import { useCallback, useEffect, useState, type ReactNode } from "react";
import {
  getIntegrationCatalog,
  listAgents,
  listApprovals,
  listConversations,
  listIntegrationConnections,
  listKnowledgeDocuments,
  listWorkflowRuns,
} from "../../lib/product-api/client";
import type {
  AgentListResponse,
  ApprovalListResponse,
  ConversationListResponse,
  ConversationView,
  IntegrationCatalogResponse,
  IntegrationConnectionListResponse,
  KnowledgeDocumentListResponse,
  ProductErrorKind,
  ProductResult,
  WorkflowRunListResponse,
} from "../../lib/product-api/types";
import { PageHeader } from "../shell/PageHeader";
import { useProductSession } from "../shell/ProductSessionProvider";
import { ConnectForm, HealthBadge } from "../shell/SessionControl";
import { Badge, Card, Mono, Timestamp, type Tone } from "../ui/primitives";

const LIST_LIMIT = 5;
const OPERATIONS_AGENT_ID = "operations";

export const QUICK_ACTIONS = [
  { href: "/operations?tab=analysis", label: "Analyze operations" },
  { href: "/operations?tab=report", label: "Daily report" },
  { href: "/operations?tab=ticket", label: "Create ticket" },
] as const;

const AREAS: [string, string, string][] = [
  ["/operations", "Operations", "Read-only analysis, the daily report and explicit operational tickets."],
  ["/approvals", "Approvals", "Human decisions on governed actions."],
  ["/conversations", "Conversations", "Customer conversations from connected channels (read-only)."],
  ["/workflows", "Workflows", "Workflow definitions and runs (read-only)."],
  ["/settings/agents", "Agents", "Product Agents, their availability, Skills and Tasks."],
  ["/settings/integrations", "Integrations", "Connections to external systems."],
  ["/settings/knowledge", "Knowledge", "Operating model and reference documents."],
  ["/system", "System", "Operational status of this installation (read-only)."],
];

const AVAILABILITY: Record<string, [string, Tone]> = {
  available: ["Available", "success"],
  disabled: ["Disabled", "attention"],
  unavailable: ["Unavailable", "danger"],
};

const RUN_STATUS: Record<string, [string, Tone]> = {
  pending: ["Pending", "pending"],
  running: ["Running", "pending"],
  succeeded: ["Succeeded", "success"],
  failed: ["Failed", "danger"],
  requires_human: ["Requires a human", "attention"],
  awaiting_approval: ["Awaiting approval", "attention"],
  timed_out: ["Timed out", "danger"],
};

const RISK: Record<string, string> = { medium_risk: "Medium risk", high_risk: "High risk" };

const TEST_RESULT: Record<string, [string, Tone]> = {
  never_tested: ["Never tested", "neutral"],
  success: ["Last test passed", "success"],
  failure: ["Last test failed", "danger"],
};

type Read<T> = { state: "loading" } | { state: "ok"; data: T } | { state: "failed"; error: ProductErrorKind };

/** One independent read: its own loading and failure state, re-issued only on refresh. */
function useRead<T>(load: () => Promise<ProductResult<T>>, refresh: number): Read<T> {
  const [read, setRead] = useState<Read<T>>({ state: "loading" });
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
  return read;
}

function failureText(kind: ProductErrorKind, area: string): string {
  switch (kind) {
    case "unauthenticated":
      return "Product API key not accepted.";
    case "forbidden":
      return `You don't have access to ${area}.`;
    case "service_unavailable":
      return `${area} is unavailable right now.`;
    default:
      return `${area} could not be loaded.`;
  }
}

export function OverviewPage() {
  const { apiKey, sessionEpoch } = useProductSession();
  const [refresh, setRefresh] = useState(0);

  return (
    <>
      <PageHeader
        title="Overview"
        description="The Agento control center: what needs attention across your commerce operations."
        actions={apiKey !== null ? (
          <button type="button" className="button button--ghost" onClick={() => setRefresh((value) => value + 1)}>
            Refresh overview
          </button>
        ) : null}
      />
      {apiKey === null ? (
        <Welcome />
      ) : (
        // Keyed by the session epoch: a new key or a disconnect drops every card's data.
        <Dashboard key={sessionEpoch} apiKey={apiKey} refresh={refresh} />
      )}
    </>
  );
}

function Welcome() {
  return (
    <div className="overview-welcome">
      <Card title="Agento" subtitle="AI operating layer for commerce operations.">
        <p className="form__context">
          Agento brings store operations, approvals, conversations, workflows, agents, integrations and knowledge
          together over one Product API. Connect with a Product API key to see what needs attention.
        </p>
        <div className="status-list">
          <div className="status-list__row">
            <span>Product API</span>
            <HealthBadge />
          </div>
        </div>
      </Card>
      <Card title="Connect to Product API" subtitle="The key is held in this tab's memory only; reloading forgets it.">
        <ConnectForm idPrefix="overview" />
      </Card>
      <Card title="Areas">
        <ul className="area-list">
          {AREAS.map(([href, label, description]) => (
            <li key={href}>
              <Link href={href}>{label}</Link>
              <span className="field__hint">{description}</span>
            </li>
          ))}
        </ul>
      </Card>
    </div>
  );
}

function Dashboard({ apiKey, refresh }: { apiKey: string; refresh: number }) {
  // Independent reads, issued in parallel. Each card renders from its own read.
  const agents = useRead<AgentListResponse>(useCallback(() => listAgents(apiKey), [apiKey]), refresh);
  const approvals = useRead<ApprovalListResponse>(
    useCallback(() => listApprovals(apiKey, "requested"), [apiKey]), refresh);
  const runs = useRead<WorkflowRunListResponse>(
    useCallback(() => listWorkflowRuns(apiKey, LIST_LIMIT), [apiKey]), refresh);
  const connections = useRead<IntegrationConnectionListResponse>(
    useCallback(() => listIntegrationConnections(apiKey), [apiKey]), refresh);
  const catalog = useRead<IntegrationCatalogResponse>(useCallback(() => getIntegrationCatalog(apiKey), [apiKey]), refresh);
  const documents = useRead<KnowledgeDocumentListResponse>(
    useCallback(() => listKnowledgeDocuments(apiKey), [apiKey]), refresh);
  const conversations = useRead<ConversationListResponse>(
    useCallback(() => listConversations(apiKey), [apiKey]), refresh);

  return (
    <div className="overview">
      <Signals agents={agents} approvals={approvals} runs={runs} connections={connections} />
      <QuickActions />
      <div className="overview-grid">
        <OperationsAgentCard read={agents} />
        <ApprovalsCard read={approvals} />
        <WorkflowsCard read={runs} />
        <ConversationsCard read={conversations} />
        <IntegrationsCard connections={connections} catalog={catalog} />
        <KnowledgeCard read={documents} />
      </div>
    </div>
  );
}

// ----- current signals (explicit statuses only) -------------------------------------------

type Signal = { tone: Tone; label: string; text: string; href?: string };

function Signals({ agents, approvals, runs, connections }: {
  agents: Read<AgentListResponse>;
  approvals: Read<ApprovalListResponse>;
  runs: Read<WorkflowRunListResponse>;
  connections: Read<IntegrationConnectionListResponse>;
}) {
  const { health } = useProductSession();
  const signals: Signal[] = [];
  if (health === "unavailable") {
    signals.push({ tone: "danger", label: "API unavailable", text: "The Product API did not answer its health check." });
  }
  if (agents.state === "ok") {
    const agent = agents.data.agents.find((item) => item.definition.agent_id === OPERATIONS_AGENT_ID);
    if (agent && agent.state.availability !== "available") {
      const [label] = AVAILABILITY[agent.state.availability] ?? [agent.state.availability];
      signals.push({ tone: "attention", label, text: "The Operations Agent is not available for analysis.", href: "/settings/agents" });
    }
  }
  if (approvals.state === "ok" && approvals.data.approvals.length > 0) {
    const count = approvals.data.approvals.length;
    signals.push({
      tone: "attention",
      label: "Awaiting decision",
      text: `${count} approval ${count === 1 ? "request is" : "requests are"} awaiting a decision.`,
      href: "/approvals",
    });
  }
  if (runs.state === "ok") {
    const failed = runs.data.runs.filter((run) => run.status === "failed" || run.status === "timed_out").length;
    const waiting = runs.data.runs.filter((run) => run.status === "requires_human" || run.status === "awaiting_approval").length;
    if (failed) {
      signals.push({ tone: "danger", label: "Failed", text: `${failed} of the most recent Workflow runs failed or timed out.`, href: "/workflows" });
    }
    if (waiting) {
      signals.push({ tone: "attention", label: "Needs a person", text: `${waiting} of the most recent Workflow runs need a person.`, href: "/workflows" });
    }
  }
  if (connections.state === "ok") {
    const failing = connections.data.connections.filter((item) => item.enabled && item.last_test_result === "failure").length;
    if (failing) {
      signals.push({ tone: "danger", label: "Test failed", text: `${failing} enabled ${failing === 1 ? "connection" : "connections"} failed the last test.`, href: "/settings/integrations" });
    }
  }
  const loading = [agents, approvals, runs, connections].some((read) => read.state === "loading");
  const partial = [agents, approvals, runs, connections].some((read) => read.state === "failed");

  return (
    <Card title="Needs attention" subtitle="Current signals, derived only from statuses the Product reported.">
      {signals.length > 0 ? (
        <ul className="signal-list">
          {signals.map((signal) => (
            <li key={signal.text} className="signal-list__item">
              <Badge tone={signal.tone}>{signal.label}</Badge>
              <span>{signal.text}</span>
              {signal.href ? <Link href={signal.href}>Open</Link> : null}
            </li>
          ))}
        </ul>
      ) : loading ? (
        <p className="loading">Loading…</p>
      ) : (
        <p className="empty">Nothing needs attention in the areas that loaded.</p>
      )}
      {partial ? <p className="field__hint">Some areas could not be read; their cards say why.</p> : null}
    </Card>
  );
}

function QuickActions() {
  return (
    <section className="quick-actions" aria-label="Quick actions">
      <h2 className="quick-actions__title">Quick actions</h2>
      <p className="field__hint">These open Operations; nothing runs until you submit there.</p>
      <ul className="quick-actions__list">
        {QUICK_ACTIONS.map((action) => (
          <li key={action.href}>
            <Link className="button button--secondary" href={action.href}>{action.label}</Link>
          </li>
        ))}
      </ul>
    </section>
  );
}

// ----- cards ---------------------------------------------------------------------------------

function ReadState<T>({ read, area, children }: { read: Read<T>; area: string; children: (data: T) => ReactNode }) {
  if (read.state === "loading") return <p className="loading">Loading…</p>;
  if (read.state === "failed") {
    return <p className="notice notice--attention" role="status">{failureText(read.error, area)}</p>;
  }
  return <>{children(read.data)}</>;
}

function CardLinks({ links }: { links: [string, string][] }) {
  return (
    <p className="card-links">
      {links.map(([href, label]) => <Link key={href} href={href}>{label}</Link>)}
    </p>
  );
}

function OperationsAgentCard({ read }: { read: Read<AgentListResponse> }) {
  return (
    <Card title="Operations Agent">
      <ReadState read={read} area="Agents">
        {(data) => {
          const agent = data.agents.find((item) => item.definition.agent_id === OPERATIONS_AGENT_ID);
          if (!agent) return <p className="empty">The Operations Agent is not part of this build.</p>;
          const [label, tone] = AVAILABILITY[agent.state.availability] ?? [agent.state.availability, "neutral" as Tone];
          return (
            <div className="status-list">
              <div className="status-list__row">
                <span>{agent.definition.name}</span>
                <Badge tone={tone}>{label}</Badge>
              </div>
              {agent.state.reason ? <p className="field__hint">Reason: <Mono>{agent.state.reason}</Mono></p> : null}
            </div>
          );
        }}
      </ReadState>
      <CardLinks links={[["/operations", "Open Operations"], ["/settings/agents", "Manage Agents"]]} />
    </Card>
  );
}

function ApprovalsCard({ read }: { read: Read<ApprovalListResponse> }) {
  return (
    <Card title="Approvals" subtitle="Requests awaiting a decision.">
      <ReadState read={read} area="Approvals">
        {(data) =>
          data.approvals.length === 0 ? (
            <p className="empty">No approval requests are awaiting a decision.</p>
          ) : (
            <>
              <p className="form__context">
                {data.approvals.length} pending {data.approvals.length === 1 ? "request" : "requests"} returned.
              </p>
              <ul className="overview-list">
                {data.approvals.slice(0, LIST_LIMIT).map((approval) => (
                  <li key={approval.approval_id}>
                    <strong className="overview-list__title">{approval.summary.title}</strong>
                    <span className="field__hint">
                      <Badge tone={approval.risk === "high_risk" ? "danger" : "attention"}>
                        {RISK[approval.risk] ?? approval.risk}
                      </Badge>{" "}
                      expires <Timestamp value={approval.expires_at} />
                    </span>
                  </li>
                ))}
              </ul>
            </>
          )
        }
      </ReadState>
      <CardLinks links={[["/approvals", "Open Approvals"]]} />
    </Card>
  );
}

function WorkflowsCard({ read }: { read: Read<WorkflowRunListResponse> }) {
  return (
    <Card title="Workflows" subtitle="Most recent runs.">
      <ReadState read={read} area="Workflows">
        {(data) =>
          data.runs.length === 0 ? (
            <p className="empty">No Workflow runs yet.</p>
          ) : (
            <ul className="overview-list">
              {data.runs.slice(0, LIST_LIMIT).map((run) => {
                const [label, tone] = RUN_STATUS[run.status] ?? [run.status, "neutral" as Tone];
                return (
                  <li key={run.run_id}>
                    <span><Mono>{run.workflow_id}</Mono> <Badge tone={tone}>{label}</Badge></span>
                    <span className="field__hint">updated <Timestamp value={run.updated_at} /></span>
                  </li>
                );
              })}
            </ul>
          )
        }
      </ReadState>
      <CardLinks links={[["/workflows", "Open Workflows"]]} />
    </Card>
  );
}

function channelLabel(conversation: ConversationView): string {
  const channel = conversation.channel;
  const integration = channel.integration_name ?? channel.integration_id;
  return channel.connection_name ? `${channel.connection_name} · ${integration}` : `${integration} (connection removed)`;
}

function ConversationsCard({ read }: { read: Read<ConversationListResponse> }) {
  return (
    <Card title="Conversations" subtitle="Recent activity (message text is not shown here).">
      <ReadState read={read} area="Conversations">
        {(data) =>
          data.conversations.length === 0 ? (
            <p className="empty">No conversations yet.</p>
          ) : (
            <ul className="overview-list">
              {data.conversations.slice(0, LIST_LIMIT).map((conversation) => (
                <li key={conversation.conversation_id}>
                  <strong className="overview-list__title">{channelLabel(conversation)}</strong>
                  <span className="field__hint">last activity <Timestamp value={conversation.last_message_at} /></span>
                </li>
              ))}
            </ul>
          )
        }
      </ReadState>
      <CardLinks links={[["/conversations", "Open Conversations"]]} />
    </Card>
  );
}

function IntegrationsCard({ connections, catalog }: {
  connections: Read<IntegrationConnectionListResponse>;
  catalog: Read<IntegrationCatalogResponse>;
}) {
  return (
    <Card title="Integrations">
      <ReadState read={catalog} area="Integrations">
        {(catalogData) =>
          catalogData.integrations.length === 0 ? (
            <p className="empty">No integrations are installed in this build.</p>
          ) : (
            <ReadState read={connections} area="Integrations">
              {(data) =>
                data.connections.length === 0 ? (
                  <p className="empty">No connections yet.</p>
                ) : (
                  <ul className="overview-list">
                    {data.connections.slice(0, LIST_LIMIT).map((connection) => {
                      const [label, tone] = TEST_RESULT[connection.last_test_result] ??
                        [connection.last_test_result, "neutral" as Tone];
                      return (
                        <li key={connection.connection_id}>
                          <strong className="overview-list__title">{connection.display_name}</strong>
                          <span className="field__hint">
                            <Badge tone={connection.enabled ? "success" : "neutral"}>
                              {connection.enabled ? "Enabled" : "Disabled"}
                            </Badge>{" "}
                            <Badge tone={tone}>{label}</Badge>
                          </span>
                        </li>
                      );
                    })}
                  </ul>
                )
              }
            </ReadState>
          )
        }
      </ReadState>
      <CardLinks links={[["/settings/integrations", "Manage Integrations"]]} />
    </Card>
  );
}

function KnowledgeCard({ read }: { read: Read<KnowledgeDocumentListResponse> }) {
  return (
    <Card title="Knowledge">
      <ReadState read={read} area="Knowledge">
        {(data) => {
          const active = data.documents.filter((document) => document.lifecycle === "active");
          if (active.length === 0) return <p className="empty">No active knowledge documents yet.</p>;
          return (
            <>
              <div className="status-list">
                <div className="status-list__row">
                  <span>{active.length} active {active.length === 1 ? "document" : "documents"}</span>
                  <Badge tone="success">Ready</Badge>
                </div>
              </div>
              <ul className="overview-list">
                {active.slice(0, LIST_LIMIT).map((document) => (
                  <li key={document.document_id}>
                    <strong className="overview-list__title">{document.title}</strong>
                    <span className="field__hint">{document.category}</span>
                  </li>
                ))}
              </ul>
            </>
          );
        }}
      </ReadState>
      <CardLinks links={[["/settings/knowledge", "Manage Knowledge"]]} />
    </Card>
  );
}
