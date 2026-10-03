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
  skill_ids: string[];
  task_ids: string[];
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

// ----- Product Skills and Tasks (Task 033): immutable, read-only Product metadata ------------

export type SkillView = {
  skill_id: string;
  name: string;
  description: string;
  category: string;
  lifecycle: string;
  capabilities: string[];
  tool_ids: string[];
  requirements: string[];
  agent_ids: string[];
  task_ids: string[];
};

export type TaskInputFieldView = {
  name: string;
  label: string;
  kind: string;
  required: boolean;
  description: string;
  max_length: number | null;
};

export type AcceptanceCriterionView = { code: string; description: string };

/** Declared envelope: contract metadata, not a security boundary. */
export type TaskLimitsView = {
  max_tool_calls: number;
  writes_possible: boolean;
  allowed_write_actions: string[];
  requires_explicit_write_intent: boolean;
};

export type TaskView = {
  task_id: string;
  name: string;
  description: string;
  category: string;
  lifecycle: string;
  skill_ids: string[];
  inputs: TaskInputFieldView[];
  acceptance_criteria: AcceptanceCriterionView[];
  limits: TaskLimitsView;
  agent_ids: string[];
  /** The deterministic Product Workflow that performs this Task (null: none). */
  workflow_id: string | null;
};

export type SkillCatalogResponse = { request_id: string; skills: SkillView[] };
export type SkillResponse = { request_id: string; skill: SkillView };
export type TaskCatalogResponse = { request_id: string; tasks: TaskView[] };
export type TaskResponse = { request_id: string; task: TaskView };

// ----- Product Workflows (Task 034; read-only inspection) ------------------------------------

export type WorkflowInputFieldView = {
  name: string;
  label: string;
  kind: string;
  required: boolean;
  description: string;
};

export type WorkflowStepView = {
  step_id: string;
  name: string;
  description: string;
  handler_id: string;
  side_effect: string; // read_only | governed_write
  timeout_seconds: number;
  max_attempts: number;
  checkpoint_policy: string; // none | state
};

export type WorkflowDefinitionView = {
  workflow_id: string;
  name: string;
  description: string;
  category: string;
  version: number;
  lifecycle: string;
  inputs: WorkflowInputFieldView[];
  steps: WorkflowStepView[];
};

export type WorkflowRunView = {
  run_id: string;
  workflow_id: string;
  workflow_version: number;
  request_id: string;
  status: string;
  current_step_id: string | null;
  failure_code: string | null;
  attempt_count: number;
  created_at: string;
  updated_at: string;
  completed_at: string | null;
};

export type StepAttemptView = {
  step_id: string;
  attempt: number;
  handler_id: string;
  status: string;
  failure_code: string | null;
  verification_code: string | null;
  started_at: string;
  completed_at: string | null;
};

export type WorkflowEventView = {
  sequence: number;
  event_type: string;
  step_id: string | null;
  attempt: number | null;
  status: string | null;
  failure_code: string | null;
  occurred_at: string;
};

export type WorkflowCatalogResponse = { request_id: string; workflows: WorkflowDefinitionView[] };
export type WorkflowResponse = { request_id: string; workflow: WorkflowDefinitionView };
export type WorkflowRunListResponse = { request_id: string; runs: WorkflowRunView[] };
export type WorkflowRunResponse = {
  request_id: string;
  run: WorkflowRunView;
  attempts: StepAttemptView[];
  events: WorkflowEventView[];
};

// ----- Task 035: Product Knowledge & company operating context -----------------------------

export type OperatingModelView = {
  version: number;
  content_hash: string;
  created_at: string;
  model: Record<string, unknown>; // the validated CompanyOperatingModel (rendered as text)
};

export type OperatingModelVersionView = {
  version: number;
  content_hash: string;
  created_at: string;
  current: boolean;
};

export type KnowledgeDocumentView = {
  document_id: string;
  category: string; // sop | policy | pricing | returns | shipping | supplier | general
  lifecycle: string; // active | archived
  current_version: number;
  title: string;
  created_at: string;
  updated_at: string;
};

export type KnowledgeDocumentVersionView = {
  version: number;
  title: string;
  content_type: string; // text/plain | text/markdown
  content_hash: string;
  created_at: string;
};

export type KnowledgeDocumentContentView = KnowledgeDocumentVersionView & {
  body: string; // untrusted reference text: rendered inertly, never as HTML
  trust: string; // untrusted_reference
};

export type KnowledgeReferenceView = {
  document_id: string;
  document_version: number;
  category: string;
  title: string;
  chunk_index: number;
  excerpt: string; // untrusted reference text
  relevance: number;
  trust: string; // untrusted_reference
};

export type OperatingModelResponse = { request_id: string; operating_model: OperatingModelView | null };
export type OperatingModelVersionsResponse = { request_id: string; versions: OperatingModelVersionView[] };
export type KnowledgeDocumentListResponse = { request_id: string; documents: KnowledgeDocumentView[] };
export type KnowledgeDocumentResponse = {
  request_id: string;
  document: KnowledgeDocumentView;
  current: KnowledgeDocumentContentView;
  versions: KnowledgeDocumentVersionView[];
};
export type KnowledgeDocumentVersionResponse = {
  request_id: string;
  document_id: string;
  version: KnowledgeDocumentContentView;
};
export type KnowledgeQueryResponse = {
  request_id: string;
  precedence: string[];
  structured: { available: boolean; authority: string; operating_model: OperatingModelView | null };
  references_trust: string;
  references: KnowledgeReferenceView[];
};
export type KnowledgeDocumentInput = { title: string; content_type: string; body: string };

// ----- Human approvals (Task 036) ------------------------------------------------------------
// Requests exist only because Product governance required a human decision; there is no
// create call. Summaries, notes and identifiers are data, rendered as plain text.

export type ApprovalStatus = "requested" | "approved" | "rejected" | "expired" | "cancelled";

export type ApprovalChangeView = { code: string; label: string; before: string | null; after: string | null };

export type ApprovalView = {
  approval_id: string;
  action_name: string;
  risk: string;
  status: ApprovalStatus;
  requester_actor_id: string;
  requester_actor_type: string;
  store_id: string | null;
  created_at: string;
  expires_at: string;
  decided_at: string | null;
  decided_by_actor_id: string | null;
  decided_by_actor_type: string | null;
  decision_note: string | null; // inert human text, never instructions
  summary: { title: string; description: string; changes: ApprovalChangeView[] };
  source: {
    kind: string; // action | write_command | workflow_step
    command_id: string | null;
    workflow_run_id: string | null;
    workflow_id: string | null;
    workflow_step_id: string | null;
  };
  action_run_id: string;
  consumed: boolean;
  consumed_by_action_run_id: string | null;
  execution_outcome: string | null;
};

export type ApprovalEventView = {
  sequence: number;
  event_type: string;
  status: ApprovalStatus;
  actor_id: string | null;
  actor_type: string | null;
  action_run_id: string | null;
  execution_outcome: string | null;
  occurred_at: string;
};

export type ApprovalListResponse = { request_id: string; approvals: ApprovalView[] };
export type ApprovalResponse = { request_id: string; approval: ApprovalView; events: ApprovalEventView[] };
export type ApprovalWorkflowResumeResponse = {
  request_id: string;
  workflow_run_id: string;
  workflow_id: string;
  status: string;
  failure_code: string | null;
};

// ----- Conversations (Task 037; read-only) ---------------------------------------------------
// Message text is UNTRUSTED external data: rendered as plain text only.

export type ConversationChannelView = {
  connection_id: string;
  integration_id: string;
  integration_name: string | null; // null: not installed in this build
  connection_name: string | null; // null: the connection was removed
};

export type ConversationView = {
  conversation_id: string;
  store_id: string | null;
  external_conversation_ref: string;
  channel: ConversationChannelView;
  created_at: string;
  last_message_at: string;
  last_message_id: string | null;
};

export type ConversationMessageView = {
  message_id: string;
  sequence: number;
  direction: "inbound" | "outbound";
  author_kind: string;
  external_sender_ref: string | null;
  text: string; // untrusted external text when inbound
  occurred_at: string; // source time
  recorded_at: string; // Product time
  delivery_state: string;
};

export type ConversationListResponse = { request_id: string; conversations: ConversationView[] };
export type ConversationResponse = { request_id: string; conversation: ConversationView };
export type ConversationMessagesResponse = {
  request_id: string;
  conversation_id: string;
  messages: ConversationMessageView[];
  next_before_sequence: number | null;
};

// ----- System Status (Task 039): stable states and codes only ------------------------------

export type SystemComponentState = "ready" | "starting" | "unavailable" | "mismatch";

export type SystemStatusResponse = {
  request_id: string;
  application: { version: string; environment: string; uptime_seconds: number };
  overall: "ready" | "not_ready";
  reasons: string[]; // stable codes, e.g. schema_mismatch
  components: {
    application: SystemComponentState;
    database: SystemComponentState;
    product_schema: SystemComponentState;
    agent_runtime: SystemComponentState;
  };
  observability: { export_mode: string }; // never the endpoint
};
