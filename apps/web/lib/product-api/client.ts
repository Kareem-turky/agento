// Browser client for the same-origin Product BFF. Explicit functions only: there is no
// generic request(path) and no way to choose an upstream. The Product API key is a
// per-call argument held by the caller in memory; this module never stores, caches,
// logs or puts it in a URL.
import type {
  AgentCatalogResponse,
  AgentListResponse,
  AgentResponse,
  DailyOperationsReportResponse,
  HealthResponse,
  IntegrationCatalogResponse,
  IntegrationConnectionDeletedResponse,
  IntegrationConnectionListResponse,
  IntegrationConnectionResponse,
  OperationsRunResponse,
  ProductErrorKind,
  ProductResult,
  TicketCommandStatusResponse,
  TicketCreateResponse,
} from "./types";

const PATHS = {
  health: "/api/product/health",
  runs: "/api/product/operations/runs",
  dailyReport: "/api/product/operations/reports/daily",
  tickets: "/api/product/operations/tickets",
  ticketCommands: "/api/product/operations/tickets/commands",
  integrationsCatalog: "/api/product/integrations/catalog",
  integrationConnections: "/api/product/integrations/connections",
  integrationConnection: "/api/product/integrations/connection",
  integrationCredentials: "/api/product/integrations/connection/credentials",
  integrationTest: "/api/product/integrations/connection/test",
  integrationEnable: "/api/product/integrations/connection/enable",
  integrationDisable: "/api/product/integrations/connection/disable",
  agentCatalog: "/api/product/agent-management/catalog",
  agentList: "/api/product/agent-management/agents",
  agentDetail: "/api/product/agent-management/agent",
  agentEnable: "/api/product/agent-management/agent/enable",
  agentDisable: "/api/product/agent-management/agent/disable",
  agentReset: "/api/product/agent-management/agent/configuration",
} as const;

type Guard<T> = (value: unknown) => value is T;

function classify(status: number): ProductErrorKind {
  switch (status) {
    case 400:
    case 422:
      return "invalid";
    case 401:
      return "unauthenticated";
    case 403:
      return "forbidden";
    case 404:
      return "not_found";
    case 409:
      return "conflict";
    case 413:
      return "too_large";
    case 503:
      return "service_unavailable";
    default:
      return "unavailable";
  }
}

async function send<T>(path: string, init: RequestInit, guard: Guard<T>): Promise<ProductResult<T>> {
  let response: Response;
  try {
    response = await fetch(path, { ...init, cache: "no-store", credentials: "omit", redirect: "error" });
  } catch {
    return { ok: false, status: null, error: "unavailable" };
  }
  if (!response.ok) {
    await response.body?.cancel().catch(() => undefined);
    return { ok: false, status: response.status, error: classify(response.status) };
  }
  let data: unknown;
  try {
    data = await response.json();
  } catch {
    return { ok: false, status: response.status, error: "unavailable" };
  }
  if (!guard(data)) return { ok: false, status: response.status, error: "unavailable" };
  return { ok: true, status: response.status, data };
}

function authorized(apiKey: string, extra: Record<string, string> = {}): Headers {
  const headers = new Headers(extra);
  headers.set("Authorization", `Bearer ${apiKey}`);
  return headers;
}

// ----- shape guards (types only; no business rules) ---------------------------------------

const isObject = (v: unknown): v is Record<string, unknown> => typeof v === "object" && v !== null && !Array.isArray(v);
const isString = (v: unknown): v is string => typeof v === "string";
const isNullableString = (v: unknown): v is string | null => v === null || typeof v === "string";
const isNumber = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);
const isStatusCounts = (v: unknown): boolean =>
  Array.isArray(v) && v.every((c) => isObject(c) && isString(c.status) && isNumber(c.count));

const isHealth: Guard<HealthResponse> = (v): v is HealthResponse => isObject(v) && isString(v.status);

const isRun: Guard<OperationsRunResponse> = (v): v is OperationsRunResponse =>
  isObject(v) && isString(v.request_id) && isString(v.message);

const isReport: Guard<DailyOperationsReportResponse> = (v): v is DailyOperationsReportResponse => {
  if (!isObject(v) || !isString(v.request_id) || !isObject(v.report)) return false;
  const r = v.report;
  const m = r.metrics;
  const c = r.coverage;
  return (
    isString(r.business_date) && isString(r.timezone) && isString(r.generated_at) &&
    isObject(m) && isNumber(m.orders_created) && isNumber(m.shipments_shipped) &&
    isNumber(m.affected_orders) && isStatusCounts(m.order_status_counts) &&
    isStatusCounts(m.shipment_status_counts) && Array.isArray(r.findings) &&
    r.findings.every((f) => isObject(f) && isString(f.code) && isString(f.severity)) &&
    isNumber(r.findings_total) && typeof r.findings_truncated === "boolean" &&
    isObject(c) && isString(c.inventory) && isString(c.inventory_reason)
  );
};

const isTicket: Guard<TicketCreateResponse> = (v): v is TicketCreateResponse =>
  isObject(v) && isString(v.request_id) && isString(v.command_id) && isString(v.status) &&
  isNullableString(v.reason) && isNullableString(v.ticket_id) &&
  typeof v.replayed === "boolean" && typeof v.persistence_complete === "boolean";

const isCommand: Guard<TicketCommandStatusResponse> = (v): v is TicketCommandStatusResponse =>
  isObject(v) && isString(v.request_id) && isString(v.command_id) && isString(v.status) &&
  isNullableString(v.reason) && isNullableString(v.ticket_id) &&
  isString(v.created_at) && isString(v.updated_at);

const isStringArray = (v: unknown): v is string[] => Array.isArray(v) && v.every(isString);

const isDefinition = (v: unknown): boolean =>
  isObject(v) && isString(v.integration_id) && isString(v.name) && isString(v.category) &&
  isString(v.description) && isString(v.auth_mode) && typeof v.connectable === "boolean" &&
  isStringArray(v.capabilities) && Array.isArray(v.fields) &&
  v.fields.every((f) => isObject(f) && isString(f.name) && isString(f.label) && isString(f.kind) &&
    typeof f.required === "boolean" && isNullableString(f.help_text));

const isConnection = (v: unknown): boolean =>
  isObject(v) && isString(v.connection_id) && isString(v.integration_id) && isString(v.display_name) &&
  isObject(v.config) && Object.values(v.config).every((c) => isString(c) || typeof c === "boolean") &&
  isStringArray(v.configured_secret_fields) && typeof v.enabled === "boolean" &&
  isString(v.created_at) && isString(v.updated_at) && isNullableString(v.last_tested_at) &&
  isString(v.last_test_result) && isNullableString(v.last_test_error);

const isCatalog: Guard<IntegrationCatalogResponse> = (v): v is IntegrationCatalogResponse =>
  isObject(v) && isString(v.request_id) && Array.isArray(v.integrations) && v.integrations.every(isDefinition);

const isConnectionList: Guard<IntegrationConnectionListResponse> = (v): v is IntegrationConnectionListResponse =>
  isObject(v) && isString(v.request_id) && Array.isArray(v.connections) && v.connections.every(isConnection);

const isConnectionResponse: Guard<IntegrationConnectionResponse> = (v): v is IntegrationConnectionResponse =>
  isObject(v) && isString(v.request_id) && isConnection(v.connection);

const isDeleted: Guard<IntegrationConnectionDeletedResponse> = (v): v is IntegrationConnectionDeletedResponse =>
  isObject(v) && isString(v.request_id) && isString(v.connection_id) && typeof v.deleted === "boolean";

const isAgentDefinition = (v: unknown): boolean => {
  if (!isObject(v) || !isObject(v.manifest)) return false;
  const m = v.manifest;
  return (
    isString(v.agent_id) && isString(v.name) && isString(v.description) && isString(v.category) &&
    isString(v.lifecycle) && typeof v.default_enabled === "boolean" && isStringArray(v.capabilities) &&
    isNumber(m.tool_call_limit) && isStringArray(m.action_names) && isStringArray(m.requirements) &&
    isStringArray(m.safety) && Array.isArray(m.tools) &&
    m.tools.every((t) => isObject(t) && isString(t.tool_id) && isString(t.access) &&
      isStringArray(t.action_names) && isString(t.description))
  );
};

const isAgentView = (v: unknown): boolean =>
  isObject(v) && isAgentDefinition(v.definition) && isObject(v.state) &&
  typeof v.state.enabled === "boolean" && isString(v.state.source) &&
  isString(v.state.availability) && isNullableString(v.state.reason) &&
  isNullableString(v.state.updated_at);

const isAgentList: Guard<AgentListResponse> = (v): v is AgentListResponse =>
  isObject(v) && isString(v.request_id) && Array.isArray(v.agents) && v.agents.every(isAgentView);

const isAgentCatalog: Guard<AgentCatalogResponse> = (v): v is AgentCatalogResponse =>
  isObject(v) && isString(v.request_id) && Array.isArray(v.agents) && v.agents.every(isAgentDefinition);

const isAgentResponse: Guard<AgentResponse> = (v): v is AgentResponse =>
  isObject(v) && isString(v.request_id) && isAgentView(v.agent);

// ----- the Product functions ---------------------------------------------------------------

export function getHealth(): Promise<ProductResult<HealthResponse>> {
  return send(PATHS.health, { method: "GET" }, isHealth);
}

export function runOperations(apiKey: string, storeId: string, message: string): Promise<ProductResult<OperationsRunResponse>> {
  return send(
    PATHS.runs,
    {
      method: "POST",
      headers: authorized(apiKey, { "Content-Type": "application/json" }),
      body: JSON.stringify({ message, store_id: storeId }),
    },
    isRun,
  );
}

export function getDailyReport(
  apiKey: string,
  storeId: string,
  businessDate?: string,
): Promise<ProductResult<DailyOperationsReportResponse>> {
  const query = new URLSearchParams({ store_id: storeId });
  if (businessDate) query.set("business_date", businessDate); // blank: omitted entirely
  return send(`${PATHS.dailyReport}?${query.toString()}`, { method: "GET", headers: authorized(apiKey) }, isReport);
}

export function createTicket(
  apiKey: string,
  ticket: { storeId: string; title: string; description: string },
  idempotencyKey: string,
): Promise<ProductResult<TicketCreateResponse>> {
  return send(
    PATHS.tickets,
    {
      method: "POST",
      headers: authorized(apiKey, {
        "Content-Type": "application/json",
        "Idempotency-Key": idempotencyKey,
      }),
      body: JSON.stringify({ store_id: ticket.storeId, title: ticket.title, description: ticket.description }),
    },
    isTicket,
  );
}

export function getTicketCommand(apiKey: string, commandId: string): Promise<ProductResult<TicketCommandStatusResponse>> {
  const query = new URLSearchParams({ command_id: commandId });
  return send(`${PATHS.ticketCommands}?${query.toString()}`, { method: "GET", headers: authorized(apiKey) }, isCommand);
}

// ----- integration management (metadata only; secret values are write-only) -----------------

function connectionQuery(path: string, connectionId: string): string {
  return `${path}?${new URLSearchParams({ connection_id: connectionId }).toString()}`;
}

export function getIntegrationCatalog(apiKey: string): Promise<ProductResult<IntegrationCatalogResponse>> {
  return send(PATHS.integrationsCatalog, { method: "GET", headers: authorized(apiKey) }, isCatalog);
}

export function listIntegrationConnections(apiKey: string): Promise<ProductResult<IntegrationConnectionListResponse>> {
  return send(PATHS.integrationConnections, { method: "GET", headers: authorized(apiKey) }, isConnectionList);
}

export function getIntegrationConnection(
  apiKey: string,
  connectionId: string,
): Promise<ProductResult<IntegrationConnectionResponse>> {
  return send(connectionQuery(PATHS.integrationConnection, connectionId), { method: "GET", headers: authorized(apiKey) }, isConnectionResponse);
}

/** Credentials go only into this one request body; they are never kept by this module. */
export function createIntegrationConnection(
  apiKey: string,
  request: {
    integrationId: string;
    displayName: string;
    config: Record<string, string | boolean>;
    credentials: Record<string, string>;
  },
): Promise<ProductResult<IntegrationConnectionResponse>> {
  return send(
    PATHS.integrationConnections,
    {
      method: "POST",
      headers: authorized(apiKey, { "Content-Type": "application/json" }),
      body: JSON.stringify({
        integration_id: request.integrationId,
        display_name: request.displayName,
        config: request.config,
        credentials: request.credentials,
      }),
    },
    isConnectionResponse,
  );
}

/** Name and non-secret configuration only: this request can never clear credentials. */
export function updateIntegrationConnection(
  apiKey: string,
  connectionId: string,
  update: { displayName: string; config: Record<string, string | boolean> },
): Promise<ProductResult<IntegrationConnectionResponse>> {
  return send(
    connectionQuery(PATHS.integrationConnection, connectionId),
    {
      method: "PUT",
      headers: authorized(apiKey, { "Content-Type": "application/json" }),
      body: JSON.stringify({ display_name: update.displayName, config: update.config }),
    },
    isConnectionResponse,
  );
}

/** Explicit, complete credential replacement (the old set stays until this succeeds). */
export function replaceIntegrationCredentials(
  apiKey: string,
  connectionId: string,
  credentials: Record<string, string>,
): Promise<ProductResult<IntegrationConnectionResponse>> {
  return send(
    connectionQuery(PATHS.integrationCredentials, connectionId),
    {
      method: "PUT",
      headers: authorized(apiKey, { "Content-Type": "application/json" }),
      body: JSON.stringify({ credentials }),
    },
    isConnectionResponse,
  );
}

export function testIntegrationConnection(
  apiKey: string,
  connectionId: string,
): Promise<ProductResult<IntegrationConnectionResponse>> {
  return send(connectionQuery(PATHS.integrationTest, connectionId), { method: "POST", headers: authorized(apiKey) }, isConnectionResponse);
}

export function setIntegrationConnectionEnabled(
  apiKey: string,
  connectionId: string,
  enabled: boolean,
): Promise<ProductResult<IntegrationConnectionResponse>> {
  const path = enabled ? PATHS.integrationEnable : PATHS.integrationDisable;
  return send(connectionQuery(path, connectionId), { method: "POST", headers: authorized(apiKey) }, isConnectionResponse);
}

export function deleteIntegrationConnection(
  apiKey: string,
  connectionId: string,
): Promise<ProductResult<IntegrationConnectionDeletedResponse>> {
  return send(connectionQuery(PATHS.integrationConnection, connectionId), { method: "DELETE", headers: authorized(apiKey) }, isDeleted);
}

// ----- Product Agent management (Product API only, never AgentOS) ------------------------------

function agentQuery(path: string, agentId: string): string {
  return `${path}?${new URLSearchParams({ agent_id: agentId }).toString()}`;
}

export function getAgentCatalog(apiKey: string): Promise<ProductResult<AgentCatalogResponse>> {
  return send(PATHS.agentCatalog, { method: "GET", headers: authorized(apiKey) }, isAgentCatalog);
}

export function getAgent(apiKey: string, agentId: string): Promise<ProductResult<AgentResponse>> {
  return send(agentQuery(PATHS.agentDetail, agentId), { method: "GET", headers: authorized(apiKey) }, isAgentResponse);
}

export function listAgents(apiKey: string): Promise<ProductResult<AgentListResponse>> {
  return send(PATHS.agentList, { method: "GET", headers: authorized(apiKey) }, isAgentList);
}

export function setAgentEnabled(apiKey: string, agentId: string, enabled: boolean): Promise<ProductResult<AgentResponse>> {
  const path = enabled ? PATHS.agentEnable : PATHS.agentDisable;
  return send(agentQuery(path, agentId), { method: "POST", headers: authorized(apiKey) }, isAgentResponse);
}

export function resetAgentConfiguration(apiKey: string, agentId: string): Promise<ProductResult<AgentResponse>> {
  return send(agentQuery(PATHS.agentReset, agentId), { method: "DELETE", headers: authorized(apiKey) }, isAgentResponse);
}

/** A fresh idempotency key for one ticket intent (UUID v4). */
export function newIdempotencyKey(): string {
  if (typeof crypto.randomUUID === "function") return crypto.randomUUID();
  // Non-secure contexts (plain http on a non-localhost host) lack randomUUID.
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** UX-only shape check; the backend stays authoritative for every store and command. */
export function looksLikeUuid(value: string): boolean {
  return UUID.test(value.trim());
}
