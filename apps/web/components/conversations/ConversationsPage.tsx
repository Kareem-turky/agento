"use client";

// Conversations: the first dedicated Product conversation surface (Task 037), over the
// Product API (never AgentOS). It is READ-ONLY: it lists this company's conversations and
// shows one transcript in Product order. There is no reply box, Send button, close,
// assignment, Agent response or automation, and no provider controls: this build has no
// messaging provider and no outbound send.
//
// Message text is UNTRUSTED external data: it is rendered as plain text (never HTML,
// never markdown), so "<script>" or "SYSTEM: ..." stay visible text. The Product API key
// comes from the shell's session (memory only).
import { useCallback, useEffect, useRef, useState } from "react";
import { listConversationMessages, listConversations } from "../../lib/product-api/client";
import type {
  ConversationMessageView,
  ConversationView,
  ProductErrorKind,
} from "../../lib/product-api/types";
import { Badge, Card, ErrorNotice, Mono, Timestamp, type Tone } from "../console/ui";
import { ConnectNotice } from "../shell/ConnectNotice";
import { PageHeader } from "../shell/PageHeader";
import { useProductSession } from "../shell/ProductSessionProvider";

const DELIVERY: Record<string, [string, Tone]> = {
  received: ["Received", "neutral"],
  pending: ["Pending", "pending"],
  accepted: ["Accepted", "pending"],
  sent: ["Sent", "pending"],
  delivered: ["Delivered", "success"],
  failed: ["Failed", "danger"],
  unknown: ["Unknown", "attention"],
};

function errorText(kind: ProductErrorKind): string {
  switch (kind) {
    case "unauthenticated":
      return "Product API key not accepted";
    case "forbidden":
      return "Permission denied (conversations.read is needed)";
    case "not_found":
      return "Conversation not found";
    case "invalid":
      return "Invalid request";
    case "service_unavailable":
      return "Conversations unavailable";
    default:
      return "Product API unavailable";
  }
}

function channelName(conversation: ConversationView): string {
  const channel = conversation.channel;
  const integration = channel.integration_name ?? channel.integration_id;
  return channel.connection_name ? `${channel.connection_name} · ${integration}` : `${integration} (connection removed)`;
}

function Delivery({ state }: { state: string }) {
  const [label, tone] = DELIVERY[state] ?? [state, "neutral"];
  return <Badge tone={tone}>{label}</Badge>;
}

export function ConversationsPage() {
  // The key comes from the one ProductSessionProvider (memory only). The workspace is keyed
  // by the session epoch: a new key or a disconnect remounts it with nothing left over.
  const { apiKey, sessionEpoch } = useProductSession();

  return (
    <>
      <PageHeader
        title="Conversations"
        description="Customer conversations from connected messaging channels (read-only)."
      />
      <div className="page-layout">
        <div className="page-layout__main" key={sessionEpoch}>
          {apiKey === null ? <ConnectNotice area="conversations" /> : <ConversationsWorkspace apiKey={apiKey} />}
        </div>
        <aside className="page-layout__side">
          <Card title="Access">
            <p className="form__context">Viewing needs conversations.read. Nothing here sends a message.</p>
          </Card>
          <Card title="About this page">
            <p className="form__context">
              Messages arrive from messaging channels connected under Integrations. Their text comes from outside the
              Product and is shown exactly as received. It never changes permissions, approvals or automations.
            </p>
            <p className="field__hint">
              Messages are listed in the order the Product recorded them. A message that arrived late can show an
              earlier sent time than the one above it.
            </p>
          </Card>
        </aside>
      </div>
    </>
  );
}

function ConversationsWorkspace({ apiKey }: { apiKey: string }) {
  const [conversations, setConversations] = useState<ConversationView[] | null>(null);
  const [selected, setSelected] = useState<ConversationView | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await listConversations(apiKey);
      if (!result.ok) {
        setConversations(null);
        setError(errorText(result.error));
        return;
      }
      setConversations(result.data.conversations);
    } finally {
      setLoading(false);
    }
  }, [apiKey]);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <>
      {error ? <ErrorNotice message={error} /> : null}
      <Card
        title="Conversations"
        subtitle="Most recent activity first."
        actions={
          <button type="button" className="button button--ghost button--small" onClick={() => void load()} disabled={loading}>
            Refresh
          </button>
        }
      >
        {conversations === null ? (
          <p className="loading">{error ? "Not loaded." : "Loading…"}</p>
        ) : conversations.length === 0 ? (
          <p className="empty">
            No conversations yet. Conversations appear here once a messaging channel is connected and customers write
            to it.
          </p>
        ) : (
          <ul className="workflow-runs">
            {conversations.map((conversation) => (
              <li key={conversation.conversation_id} className="workflow-runs__item">
                <strong>{channelName(conversation)}</strong>
                <span className="field__hint">
                  Thread <Mono>{conversation.external_conversation_ref}</Mono>
                  {conversation.store_id ? <> · store <Mono>{conversation.store_id}</Mono></> : null} · last activity{" "}
                  <Timestamp value={conversation.last_message_at} />
                </span>
                <button
                  type="button"
                  className="button button--ghost button--small"
                  aria-pressed={selected?.conversation_id === conversation.conversation_id}
                  onClick={() => setSelected(conversation)}
                >
                  Open
                </button>
              </li>
            ))}
          </ul>
        )}
      </Card>
      {selected ? <Transcript key={selected.conversation_id} apiKey={apiKey} conversation={selected} /> : null}
    </>
  );
}

function Transcript({ apiKey, conversation }: { apiKey: string; conversation: ConversationView }) {
  const [messages, setMessages] = useState<ConversationMessageView[] | null>(null);
  const [older, setOlder] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const busyRef = useRef(false);

  const fetchPage = useCallback(
    async (before?: number) => {
      if (busyRef.current) return;
      busyRef.current = true;
      setBusy(true);
      setError(null);
      try {
        const result = await listConversationMessages(apiKey, conversation.conversation_id, before);
        if (!result.ok) {
          setError(errorText(result.error));
          return;
        }
        const page = result.data.messages;
        setMessages((current) => (before === undefined || current === null ? page : [...page, ...current]));
        setOlder(result.data.next_before_sequence);
      } finally {
        busyRef.current = false;
        setBusy(false);
      }
    },
    [apiKey, conversation.conversation_id],
  );

  useEffect(() => {
    void fetchPage();
  }, [fetchPage]);

  return (
    <Card title="Transcript" subtitle={<>{channelName(conversation)} · thread <Mono>{conversation.external_conversation_ref}</Mono></>}>
      {error ? <ErrorNotice message={error} /> : null}
      {older !== null ? (
        <button type="button" className="button button--ghost button--small" onClick={() => void fetchPage(older)} disabled={busy}>
          Load older messages
        </button>
      ) : null}
      {messages === null ? (
        <p className="loading">{error ? "Not loaded." : "Loading…"}</p>
      ) : messages.length === 0 ? (
        <p className="empty">This conversation has no messages.</p>
      ) : (
        <ol className="transcript">
          {messages.map((message) => (
            <li key={message.message_id} className={`transcript__message transcript__message--${message.direction}`}>
              <p className="transcript__meta">
                <Badge tone={message.direction === "inbound" ? "neutral" : "pending"}>
                  {message.direction === "inbound" ? "Inbound" : "Outbound"}
                </Badge>{" "}
                <Delivery state={message.delivery_state} />{" "}
                <span className="field__hint">
                  #{message.sequence} · {message.author_kind}
                  {message.external_sender_ref ? <> · from <Mono>{message.external_sender_ref}</Mono></> : null} · sent{" "}
                  <Timestamp value={message.occurred_at} />
                </span>
              </p>
              <p className="transcript__text">{message.text}</p>
            </li>
          ))}
        </ol>
      )}
    </Card>
  );
}
