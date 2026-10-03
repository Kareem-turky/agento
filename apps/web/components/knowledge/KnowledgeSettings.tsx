"use client";

// Settings → Knowledge: the company operating model and Knowledge documents over the Product
// API (never AgentOS).
//
// * The structured operating model is DISPLAY ONLY here (current version and immutable
//   history). Publishing a new version is API-first (POST /api/v1/knowledge/operating-model/
//   publish); there is no JSON editor in this console.
// * Knowledge documents are operator-authored TEXT (text/plain or text/markdown): create,
//   publish a new version, archive (no delete, no upload, no URL import).
// * The retrieval preview shows what a future trusted consumer would receive: the
//   structured model (authoritative for its fields) and UNTRUSTED reference excerpts.
//
// Every document title, body and excerpt is untrusted text: it is rendered inertly as plain
// text (never as HTML or Markdown, never executed). The Product API key comes from the
// shell's session (memory only).
import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import {
  archiveKnowledgeDocument,
  createKnowledgeDocument,
  getKnowledgeDocument,
  getKnowledgeDocumentVersion,
  getKnowledgeOperatingModel,
  getKnowledgeOperatingModelVersion,
  listKnowledgeDocuments,
  listKnowledgeOperatingModelVersions,
  publishKnowledgeDocumentVersion,
  queryKnowledge,
} from "../../lib/product-api/client";
import type {
  KnowledgeDocumentContentView,
  KnowledgeDocumentResponse,
  KnowledgeDocumentView,
  KnowledgeQueryResponse,
  OperatingModelVersionView,
  OperatingModelView,
  ProductErrorKind,
} from "../../lib/product-api/types";
import { Badge, Card, ErrorNotice, KeyValue, Mono, Timestamp } from "../console/ui";
import { ConnectNotice } from "../shell/ConnectNotice";
import { PageHeader } from "../shell/PageHeader";
import { useProductSession } from "../shell/ProductSessionProvider";

const CATEGORIES = ["sop", "policy", "pricing", "returns", "shipping", "supplier", "general"] as const;
const CONTENT_TYPES = ["text/markdown", "text/plain"] as const;
const MAX_TITLE = 200;
const MAX_BODY = 50_000;
const MAX_QUERY = 256;

const PRECEDENCE_LABELS: Record<string, string> = {
  security_permissions_policy: "Security, permissions and policy",
  product_runtime_contracts: "Product runtime contracts",
  structured_operating_model: "Structured operating model",
  knowledge_document_references: "Knowledge document references (untrusted)",
};

function errorText(kind: ProductErrorKind, action = "read"): string {
  switch (kind) {
    case "unauthenticated":
      return "Product API key not accepted";
    case "forbidden":
      return action === "read" ? "Permission denied (knowledge.read is needed)" : "Permission denied (knowledge.manage is needed)";
    case "not_found":
      return "Not found";
    case "invalid":
      return "Refused: check the category, title, content type and size, or the text is identical to the current version, or the document is archived";
    case "conflict":
      return "The change did not complete; nothing was confirmed";
    case "too_large":
      return "Too large";
    case "service_unavailable":
      return "Knowledge unavailable";
    default:
      return "Product API unavailable";
  }
}

/** A short, inert text rendering of one operating-model value. */
function shown(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") return String(value);
  if (Array.isArray(value)) return value.length === 0 ? "none" : value.map((item) => shown(item)).join(", ");
  return JSON.stringify(value);
}

function field(model: Record<string, unknown>, ...path: string[]): unknown {
  let current: unknown = model;
  for (const key of path) {
    if (typeof current !== "object" || current === null || Array.isArray(current)) return undefined;
    current = (current as Record<string, unknown>)[key];
  }
  return current;
}

function Untrusted() {
  return <Badge tone="attention">Untrusted reference</Badge>;
}

/** Untrusted document text, rendered as plain text only. */
function InertText({ text }: { text: string }) {
  return <pre className="knowledge-text">{text}</pre>;
}

export function KnowledgeSettings() {
  // The key comes from the one ProductSessionProvider (memory only). The workspace is keyed
  // by the session epoch: a new key or a disconnect remounts it with nothing left over.
  const { apiKey, sessionEpoch } = useProductSession();

  return (
    <>
      <PageHeader
        title="Knowledge"
        description="The operating model and reference documents the Product may retrieve. Reference text is untrusted, never instructions."
      />
      <div className="page-layout">
        <div className="page-layout__main" key={sessionEpoch}>
          {apiKey === null ? <ConnectNotice area="knowledge" /> : <KnowledgeWorkspace apiKey={apiKey} />}
        </div>
        <aside className="page-layout__side">
          <Card title="Access">
            <p className="form__context">Viewing needs knowledge.read; changing documents needs knowledge.manage.</p>
          </Card>
          <Card title="What Knowledge is" subtitle="Reference data, never instructions.">
            <p className="form__context">
              Documents never grant permissions, define tools, workflows or policies, or reach an Agent. Do not store
              passwords, API keys or other secrets here.
            </p>
          </Card>
        </aside>
      </div>
    </>
  );
}

function KnowledgeWorkspace({ apiKey }: { apiKey: string }) {
  const [documents, setDocuments] = useState<KnowledgeDocumentView[] | null>(null);
  const [selected, setSelected] = useState<KnowledgeDocumentResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const loadDocuments = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await listKnowledgeDocuments(apiKey);
      if (!result.ok) {
        setError(errorText(result.error));
        return;
      }
      setDocuments(result.data.documents);
    } finally {
      setLoading(false);
    }
  }, [apiKey]);

  useEffect(() => {
    void loadDocuments();
  }, [loadDocuments]);

  async function open(documentId: string) {
    setError(null);
    const result = await getKnowledgeDocument(apiKey, documentId);
    if (!result.ok) {
      setSelected(null);
      setError(errorText(result.error));
      return;
    }
    setSelected(result.data);
  }

  function changed(detail: KnowledgeDocumentResponse) {
    setSelected(detail);
    void loadDocuments();
  }

  return (
    <>
      <OperatingModelCard apiKey={apiKey} />
      {error ? <ErrorNotice message={error} /> : null}
      <Card
        title="Knowledge documents"
        subtitle="Operator-authored text (SOPs, policies, pricing, returns, shipping, supplier notes). Archived documents are kept but never retrieved."
        actions={<button type="button" className="button button--ghost button--small" onClick={() => void loadDocuments()}
                         disabled={loading}>Refresh</button>}
      >
        {documents === null ? (
          <p className="loading">{error ? "Not loaded." : "Loading…"}</p>
        ) : documents.length === 0 ? (
          <p className="empty">No Knowledge documents yet.</p>
        ) : (
          <ul className="integration-list">
            {documents.map((document) => (
              <li key={document.document_id} className="integration-list__item">
                <div className="integration-list__body">
                  <p className="integration-list__title">
                    <strong>{document.title}</strong>{" "}
                    <Badge tone="neutral">{document.category}</Badge>{" "}
                    <Badge tone={document.lifecycle === "active" ? "success" : "neutral"}>{document.lifecycle}</Badge>
                  </p>
                  <p className="field__hint">
                    Version {document.current_version} · updated <Timestamp value={document.updated_at} />
                  </p>
                </div>
                <button type="button" className="button button--ghost button--small" onClick={() => void open(document.document_id)}>
                  Details
                </button>
              </li>
            ))}
          </ul>
        )}
      </Card>
      {selected ? <DocumentDetail apiKey={apiKey} detail={selected} onChanged={changed} /> : null}
      <CreateDocumentCard apiKey={apiKey} onCreated={changed} />
      <RetrievalPreviewCard apiKey={apiKey} />
    </>
  );
}

function OperatingModelSummary({ model }: { model: OperatingModelView }) {
  const m = model.model;
  const escalations = field(m, "escalations");
  return (
    <>
      <KeyValue
        items={[
          ["Version", <>v{model.version} · <Timestamp value={model.created_at} /></>],
          ["Reporting timezone", shown(field(m, "reporting", "timezone"))],
          ["Order processing SLA", shown(field(m, "order_sla", "processing_sla"))],
          ["Ship-to-delivery SLA", shown(field(m, "shipment_sla", "ship_to_delivery_sla"))],
          ["Enabled KPIs", shown(field(m, "kpis", "enabled_kpis"))],
          ["Escalation rules", Array.isArray(escalations) ? String(escalations.length) : "—"],
          ["Agent capability intent", shown(field(m, "capabilities", "enabled_agents"))],
          ["Content hash", <Mono key="hash">{model.content_hash}</Mono>],
        ]}
      />
      <details className="manifest">
        <summary>Full model (read-only)</summary>
        <InertText text={JSON.stringify(m, null, 2)} />
      </details>
    </>
  );
}

function OperatingModelCard({ apiKey }: { apiKey: string }) {
  const [current, setCurrent] = useState<OperatingModelView | null | undefined>(undefined);
  const [versions, setVersions] = useState<OperatingModelVersionView[]>([]);
  const [viewed, setViewed] = useState<OperatingModelView | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError(null);
    const [model, history] = await Promise.all([getKnowledgeOperatingModel(apiKey), listKnowledgeOperatingModelVersions(apiKey)]);
    if (!model.ok) {
      setError(errorText(model.error));
      return;
    }
    setCurrent(model.data.operating_model);
    if (history.ok) setVersions(history.data.versions);
  }, [apiKey]);

  useEffect(() => {
    void load();
  }, [load]);

  async function view(version: number) {
    const result = await getKnowledgeOperatingModelVersion(apiKey, version);
    if (!result.ok) {
      setError(errorText(result.error));
      return;
    }
    setViewed(result.data.operating_model);
  }

  return (
    <Card
      title="Company operating model"
      subtitle="Structured, validated operating context (authoritative for its fields). Publishing a new version is API-first; capabilities are operating intent and never enable an Agent."
    >
      {error ? <ErrorNotice message={error} /> : null}
      {current === undefined ? (
        <p className="loading">{error ? "Not loaded." : "Loading…"}</p>
      ) : current === null ? (
        <p className="empty">No operating model has been published yet.</p>
      ) : (
        <OperatingModelSummary model={current} />
      )}
      {versions.length > 0 ? (
        <>
          <h3 className="capabilities__title">Version history (immutable)</h3>
          <table className="workflow-table">
            <thead>
              <tr><th>Version</th><th>Published</th><th>Content hash</th><th /></tr>
            </thead>
            <tbody>
              {versions.map((version) => (
                <tr key={version.version}>
                  <td>v{version.version} {version.current ? <Badge tone="success">current</Badge> : null}</td>
                  <td><Timestamp value={version.created_at} /></td>
                  <td><Mono>{version.content_hash.slice(0, 12)}</Mono></td>
                  <td>
                    <button type="button" className="button button--ghost button--small" onClick={() => void view(version.version)}>
                      View
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      ) : null}
      {viewed ? (
        <>
          <h3 className="capabilities__title">Viewing version {viewed.version}</h3>
          <OperatingModelSummary model={viewed} />
        </>
      ) : null}
    </Card>
  );
}

function DocumentForm({
  submitLabel,
  initial,
  withCategory,
  busy,
  onSubmit,
}: {
  submitLabel: string;
  initial?: KnowledgeDocumentContentView;
  withCategory: boolean;
  busy: boolean;
  onSubmit: (input: { category: string; title: string; content_type: string; body: string }) => Promise<boolean>;
}) {
  const [category, setCategory] = useState<string>("general");
  const [title, setTitle] = useState(initial?.title ?? "");
  const [contentType, setContentType] = useState<string>(initial?.content_type ?? "text/markdown");
  const [body, setBody] = useState(initial?.body ?? "");

  async function submit(event: FormEvent) {
    event.preventDefault();
    const done = await onSubmit({ category, title, content_type: contentType, body });
    if (done && !initial) {
      setTitle("");
      setBody("");
    }
  }

  return (
    <form className="form" onSubmit={submit} autoComplete="off">
      {withCategory ? (
        <label className="field">
          <span className="field__label">Category</span>
          <select name="category" value={category} disabled={busy} onChange={(event) => setCategory(event.target.value)}>
            {CATEGORIES.map((value) => <option key={value} value={value}>{value}</option>)}
          </select>
        </label>
      ) : null}
      <label className="field">
        <span className="field__label">Title</span>
        <input name="title" value={title} maxLength={MAX_TITLE} required disabled={busy}
               onChange={(event) => setTitle(event.target.value)} />
      </label>
      <label className="field">
        <span className="field__label">Content type</span>
        <select name="content_type" value={contentType} disabled={busy} onChange={(event) => setContentType(event.target.value)}>
          {CONTENT_TYPES.map((value) => <option key={value} value={value}>{value}</option>)}
        </select>
        <span className="field__hint">Text only. Markdown is stored as text and never rendered as HTML.</span>
      </label>
      <label className="field">
        <span className="field__label">Text</span>
        <textarea name="body" rows={10} value={body} maxLength={MAX_BODY} required disabled={busy}
                  onChange={(event) => setBody(event.target.value)} />
        <span className="field__hint">{body.length} / {MAX_BODY} characters. Never paste secrets.</span>
      </label>
      <div className="form__actions">
        <button type="submit" className="button" disabled={busy || !title.trim() || !body.trim()}>{submitLabel}</button>
      </div>
    </form>
  );
}

function CreateDocumentCard({ apiKey, onCreated }: { apiKey: string; onCreated: (detail: KnowledgeDocumentResponse) => void }) {
  const [busy, setBusy] = useState(false);
  const busyRef = useRef(false);
  const [error, setError] = useState<string | null>(null);

  async function create(input: { category: string; title: string; content_type: string; body: string }): Promise<boolean> {
    if (busyRef.current) return false;
    busyRef.current = true;
    setBusy(true);
    setError(null);
    try {
      const result = await createKnowledgeDocument(apiKey, input.category, input);
      if (!result.ok) {
        setError(errorText(result.error, "write"));
        return false;
      }
      onCreated(result.data);
      return true;
    } finally {
      busyRef.current = false;
      setBusy(false);
    }
  }

  return (
    <Card title="New document" subtitle="Creates version 1 (governed and audited; the audit never holds the text).">
      {error ? <ErrorNotice message={error} /> : null}
      <DocumentForm submitLabel="Create document" withCategory busy={busy} onSubmit={create} />
    </Card>
  );
}

function DocumentDetail({
  apiKey,
  detail,
  onChanged,
}: {
  apiKey: string;
  detail: KnowledgeDocumentResponse;
  onChanged: (detail: KnowledgeDocumentResponse) => void;
}) {
  const [busy, setBusy] = useState(false);
  const busyRef = useRef(false);
  const [error, setError] = useState<string | null>(null);
  const [confirmArchive, setConfirmArchive] = useState(false);
  const [viewed, setViewed] = useState<KnowledgeDocumentContentView | null>(null);
  const document = detail.document;
  const active = document.lifecycle === "active";

  async function guarded(run: () => Promise<boolean>): Promise<boolean> {
    if (busyRef.current) return false;
    busyRef.current = true;
    setBusy(true);
    setError(null);
    try {
      return await run();
    } finally {
      busyRef.current = false;
      setBusy(false);
    }
  }

  function publish(input: { title: string; content_type: string; body: string }): Promise<boolean> {
    return guarded(async () => {
      const result = await publishKnowledgeDocumentVersion(apiKey, document.document_id, input);
      if (!result.ok) {
        setError(errorText(result.error, "write"));
        return false;
      }
      onChanged(result.data);
      return true;
    });
  }

  function archive(): Promise<boolean> {
    return guarded(async () => {
      const result = await archiveKnowledgeDocument(apiKey, document.document_id);
      setConfirmArchive(false);
      if (!result.ok) {
        setError(errorText(result.error, "write"));
        return false;
      }
      onChanged(result.data);
      return true;
    });
  }

  async function view(version: number) {
    const result = await getKnowledgeDocumentVersion(apiKey, document.document_id, version);
    if (!result.ok) {
      setError(errorText(result.error));
      return;
    }
    setViewed(result.data.version);
  }

  return (
    <Card
      title="Document details"
      subtitle={<>Document <Mono>{document.document_id}</Mono> · {document.category} · {document.lifecycle}</>}
    >
      {error ? <ErrorNotice message={error} /> : null}
      <p>
        <strong>{detail.current.title}</strong> <Badge tone="neutral">v{detail.current.version}</Badge>{" "}
        <Badge tone="neutral">{detail.current.content_type}</Badge> <Untrusted />
      </p>
      <InertText text={detail.current.body} />
      <h3 className="capabilities__title">Version history (immutable)</h3>
      <table className="workflow-table">
        <thead>
          <tr><th>Version</th><th>Title</th><th>Type</th><th>Published</th><th /></tr>
        </thead>
        <tbody>
          {detail.versions.map((version) => (
            <tr key={version.version}>
              <td>v{version.version}</td>
              <td>{version.title}</td>
              <td>{version.content_type}</td>
              <td><Timestamp value={version.created_at} /></td>
              <td>
                <button type="button" className="button button--ghost button--small" onClick={() => void view(version.version)}>
                  View
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {viewed ? (
        <>
          <h3 className="capabilities__title">Version {viewed.version}: {viewed.title} <Untrusted /></h3>
          <InertText text={viewed.body} />
        </>
      ) : null}
      {active ? (
        <>
          <h3 className="capabilities__title">Publish a new version</h3>
          <DocumentForm key={detail.current.version} submitLabel="Publish version" initial={detail.current}
                        withCategory={false} busy={busy} onSubmit={publish} />
          <div className="form__actions">
            {confirmArchive ? (
              <>
                <button type="button" className="button button--danger" disabled={busy} onClick={() => void archive()}>
                  Confirm archive
                </button>
                <button type="button" className="button button--ghost" disabled={busy} onClick={() => setConfirmArchive(false)}>
                  Cancel
                </button>
              </>
            ) : (
              <button type="button" className="button button--danger" disabled={busy} onClick={() => setConfirmArchive(true)}>
                Archive document
              </button>
            )}
          </div>
          <p className="field__hint">Archiving excludes the document from retrieval; its history stays here. There is no delete.</p>
        </>
      ) : (
        <p className="notice notice--neutral">Archived: kept for history, excluded from retrieval, no new versions.</p>
      )}
    </Card>
  );
}

function RetrievalPreviewCard({ apiKey }: { apiKey: string }) {
  const [query, setQuery] = useState("");
  const [result, setResult] = useState<KnowledgeQueryResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function search(event: FormEvent) {
    event.preventDefault();
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      const response = await queryKnowledge(apiKey, query.trim(), 5);
      if (!response.ok) {
        setResult(null);
        setError(errorText(response.error));
        return;
      }
      setResult(response.data);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card
      title="Retrieval preview"
      subtitle="What a trusted Product consumer would receive for a question. No answer is generated and no model is called."
    >
      <form className="form form--inline" onSubmit={search} autoComplete="off">
        <label className="field field--wide">
          <span className="field__label">Search text</span>
          <input name="query" value={query} maxLength={MAX_QUERY} onChange={(event) => setQuery(event.target.value)} />
        </label>
        <button type="submit" className="button" disabled={busy || !query.trim()}>Preview</button>
      </form>
      {error ? <ErrorNotice message={error} /> : null}
      {result ? (
        <>
          <h3 className="capabilities__title">Precedence (highest first)</h3>
          <ol className="capabilities__list">
            {result.precedence.map((authority) => <li key={authority}>{PRECEDENCE_LABELS[authority] ?? authority}</li>)}
          </ol>
          <p className="form__context">
            Structured operating model:{" "}
            {result.structured.available && result.structured.operating_model
              ? `available (v${result.structured.operating_model.version})`
              : "not published"}
          </p>
          {result.references.length === 0 ? (
            <p className="empty">No matching references.</p>
          ) : (
            <ul className="integration-list">
              {result.references.map((reference) => (
                <li key={`${reference.document_id}-${reference.chunk_index}`} className="integration-list__item">
                  <div className="integration-list__body">
                    <p className="integration-list__title">
                      <strong>{reference.title}</strong> <Badge tone="neutral">{reference.category}</Badge>{" "}
                      <Badge tone="neutral">v{reference.document_version}</Badge> <Untrusted />
                    </p>
                    <InertText text={reference.excerpt} />
                  </div>
                </li>
              ))}
            </ul>
          )}
        </>
      ) : null}
    </Card>
  );
}
