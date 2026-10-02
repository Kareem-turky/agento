// Narrow types for the EXISTING Product HTTP contracts only. They name the Product's
// canonical vocabulary; they do not encode any policy, permission, report rule or
// command-transition logic (the backend is authoritative for all of that).

export type HealthResponse = {
  status: string;
  agent_runtime?: { status?: string };
};

export type OperationsRunResponse = {
  request_id: string;
  message: string;
};

export type OrderStatus =
  | "draft" | "pending" | "confirmed" | "processing" | "fulfilled" | "cancelled"
  | "completed" | "unknown";

export type ShipmentStatus =
  | "pending" | "ready" | "shipped" | "in_transit" | "delivered" | "failed" | "returned"
  | "cancelled" | "unknown";

export type DailyOperationsFinding = {
  code: string;
  severity: string;
  entity_type: string;
  entity_id: string;
  order_id: string;
  canonical_status: string;
  recommended_action: string;
};

export type DailyOperationsReport = {
  store_id: string;
  business_date: string;
  timezone: string;
  window_start: string;
  window_end: string;
  generated_at: string;
  metrics: {
    orders_created: number;
    order_status_counts: { status: OrderStatus | string; count: number }[];
    shipments_shipped: number;
    shipment_status_counts: { status: ShipmentStatus | string; count: number }[];
    affected_orders: number;
  };
  findings: DailyOperationsFinding[];
  findings_total: number;
  findings_truncated: boolean;
  coverage: {
    orders: string;
    shipments: string;
    inventory: string;
    inventory_reason: string;
  };
};

export type DailyOperationsReportResponse = {
  request_id: string;
  report: DailyOperationsReport;
};

export type TicketCommandStatus =
  | "in_progress" | "denied" | "awaiting_approval" | "failed" | "requires_human" | "verified";

export type TicketCreateResponse = {
  request_id: string;
  command_id: string;
  status: TicketCommandStatus | string;
  reason: string | null;
  ticket_id: string | null;
  replayed: boolean;
  persistence_complete: boolean;
};

export type TicketCommandStatusResponse = {
  request_id: string;
  command_id: string;
  status: TicketCommandStatus | string;
  reason: string | null;
  ticket_id: string | null;
  created_at: string;
  updated_at: string;
};

/** Fixed, UI-level classification of a failed Product request (never raw details). */
export type ProductErrorKind =
  | "unauthenticated" // 401
  | "forbidden" // 403
  | "not_found" // 404
  | "conflict" // 409
  | "too_large" // 413
  | "invalid" // 400 / 422
  | "service_unavailable" // 503
  | "unavailable"; // BFF 502, network failure, unexpected response

export type ProductResult<T> =
  | { ok: true; status: number; data: T }
  | { ok: false; status: number | null; error: ProductErrorKind };

// ----- Integration management (Task 031): metadata only, never a secret value -------------

export type IntegrationCategory = "commerce" | "messaging" | "marketing" | "shipping" | "accounting";

export type ConfigFieldKind = "text" | "url" | "boolean" | "secret";

export type ConfigFieldView = {
  name: string;
  label: string;
  kind: ConfigFieldKind | string;
  required: boolean;
  help_text: string | null;
};

export type IntegrationDefinitionView = {
  integration_id: string;
  name: string;
  category: IntegrationCategory | string;
  description: string;
  auth_mode: string;
  connectable: boolean;
  fields: ConfigFieldView[];
  capabilities: string[];
};

export type IntegrationCatalogResponse = {
  request_id: string;
  integrations: IntegrationDefinitionView[];
};

export type ConnectionTestResult = "never_tested" | "success" | "failure";

export type IntegrationConnectionView = {
  connection_id: string;
  integration_id: string;
  display_name: string;
  config: Record<string, string | boolean>;
  /** Names of the secret fields that are configured. Never their values. */
  configured_secret_fields: string[];
  enabled: boolean;
  created_at: string;
  updated_at: string;
  last_tested_at: string | null;
  last_test_result: ConnectionTestResult | string;
  last_test_error: string | null;
};

export type IntegrationConnectionListResponse = {
  request_id: string;
  connections: IntegrationConnectionView[];
};

export type IntegrationConnectionResponse = {
  request_id: string;
  connection: IntegrationConnectionView;
};

export type IntegrationConnectionDeletedResponse = {
  request_id: string;
  connection_id: string;
  deleted: boolean;
};

// ----- Product Agent management (Task 032): trusted Agent lifecycle, never AgentOS -------

export type AgentToolView = {
  tool_id: string;
  access: "read" | "write" | string;
  action_names: string[];
  description: string;
};

export type AgentManifestView = {
  tools: AgentToolView[];
  action_names: string[];
  tool_call_limit: number;
  requirements: string[];
  safety: string[];
};

export type AgentDefinitionView = {
  agent_id: string;
  name: string;
  description: string;
  category: string;
  lifecycle: string;
  default_enabled: boolean;
  capabilities: string[];
  manifest: AgentManifestView;
};

export type AgentAvailability = "available" | "disabled" | "unavailable";

export type AgentStateView = {
  enabled: boolean;
  source: "default" | "override" | string;
  availability: AgentAvailability | string;
  reason: string | null;
  updated_at: string | null;
};

export type AgentView = { definition: AgentDefinitionView; state: AgentStateView };

export type AgentListResponse = { request_id: string; agents: AgentView[] };

export type AgentResponse = { request_id: string; agent: AgentView };

export type AgentCatalogResponse = { request_id: string; agents: AgentDefinitionView[] };
