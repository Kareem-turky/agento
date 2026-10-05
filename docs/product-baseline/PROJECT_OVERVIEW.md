# Agento — Project Overview

> **Document status:** Architecture baseline  
> **Last reviewed:** 2026-10-05  
> **Implementation status:** This supplemental document was prepared from approved project decisions and the FulFly API discovery. It has not yet been reconciled line-by-line with the implementation in `Kareem-turky/agento`; statements describe the approved product direction unless explicitly marked as verified.

## 1. What Agento is

Agento is an installable AI operating layer for e-commerce companies. It is designed to read operational data, understand business context, detect anomalies, recommend actions, execute approved actions when enabled, and record what happened.

Agento is not:

- A general-purpose chatbot.
- A shared multi-tenant SaaS product.
- A thin user interface over an agent framework.
- An LLM with direct access to sensitive external APIs.

Each company receives a physically isolated deployment of the same core platform. Its deployment uses only that company's configuration, data, integrations, knowledge, users, and enabled agents.

## 2. Product objective

The long-term objective is to provide one controlled operating layer across:

- Orders and fulfilment.
- Inventory and warehousing.
- Shipping and returns.
- Finance, COD, and settlements.
- Marketing and customer retention.
- Customer support and conversations.
- Analytics, anomaly detection, and forecasting.

The first release deliberately covers a much smaller scope. See [Current MVP](#10-current-mvp).

## 3. Core architectural decision

Agno is an internal agent runtime dependency. It is not the product or the owner of the commerce domain.

The platform must access Agno through internal interfaces:

- `AgentRuntimeInterface`
- `ToolInterface`
- `MemoryInterface`
- `WorkflowInterface`

`AgnoAdapter` implements those interfaces. This boundary allows the platform to update or replace the runtime without leaking Agno-specific types into the commerce domain, API, or frontend.

The conceptual dependency direction is:

```text
Agento Core Platform
        ↓
Commerce Domain
        ↓
Security, Permissions, and Policy
        ↓
Agent Runtime Adapter
        ↓
Agno
        ↓
Models, Tools, MCP servers, and external systems
```

## 4. Request and action flow

```text
Web / WhatsApp / API client
        ↓
API Gateway
        ↓
Authentication
        ↓
Company Permission Layer
        ↓
Policy Engine
        ↓
Commerce Orchestrator
        ↓
Agent / Team / Deterministic Workflow
        ↓
Runtime Adapter
        ↓
Tool or Integration Adapter
        ↓
External System
        ↓
Result Verification
        ↓
Audit Log and Business Metrics
```

The frontend never calls Agno directly. Agents never call sensitive external APIs directly. All external operations must pass through controlled tools or adapters.

## 5. Platform responsibilities

### Agento owns

- Brand and user experience.
- Commerce domain models and business rules.
- Authentication and permissions inside a company deployment.
- Policy evaluation and risk classification.
- Human approval workflows.
- Tool contracts and input validation.
- Integration adapters.
- Verification after tool execution.
- Audit logs and operational observability.
- Agent definitions and business-specific workflows.
- Analytics and business impact reporting.

### Agno owns

- Agent runtime execution.
- Model interaction primitives.
- Teams and workflow runtime primitives.
- Memory and knowledge primitives.
- MCP primitives.

Agno must be pinned to a tested version in production. The platform must not depend directly on unstable framework internals.

## 6. Security and trust model

Agento assumes that prompt injection is always possible and that the LLM is not a trusted authority.

Required rules:

1. API keys and credentials stay in secret management, never in prompts.
2. Tool results, retrieved documents, customer messages, and external API responses are untrusted inputs.
3. Every tool call is checked against the acting user's permissions and the agent manifest.
4. Inputs are validated before execution.
5. The policy engine assigns a risk level before execution.
6. Sensitive actions require human approval.
7. The result of an action is verified after execution.
8. The request, decision, execution, result, and verification status are recorded in the audit log.
9. The commerce domain must remain independent of model and integration providers.

The intended tool-security flow is:

```text
Agent request
  → Permission check
  → Policy evaluation
  → Input validation
  → Risk classification
  → Human approval when required
  → Tool execution
  → Result verification
  → Audit log
```

### Risk levels

| Level | Typical operation | Default handling |
|---|---|---|
| `READ` | Read orders or inventory | Automatic |
| `LOW_RISK_WRITE` | Create a report or draft | Automatic after validation |
| `MEDIUM_RISK` | Modify an order | Policy-dependent review |
| `HIGH_RISK` | Refund, price change, advertising budget, or money transfer | Mandatory approval |

Approval lifecycle states are `Requested`, `Approved`, `Rejected`, `Expired`, and `Cancelled`. An approval record must include the requester, approver, reason, old value, new value, and relevant timestamps.

## 7. Commerce domain

Provider-specific data must be converted at the integration boundary:

```text
Shopify Order     → Shopify Adapter → CommerceOrder
WooCommerce Order → Woo Adapter     → CommerceOrder
FulFly Order      → FulFly Adapter  → CommerceOrder
```

Planned domain entities include:

- Company, Store, User, and Role.
- Order, OrderItem, Customer, Payment, and Transaction.
- Product, Variant, Inventory, and Warehouse.
- Shipment, Courier, Return, and Refund.
- COD and Settlement.
- Supplier and PurchaseOrder.
- Expense and Revenue.
- Conversation and Ticket.
- Campaign, AdAccount, and CampaignMetrics.
- Agent, AgentRun, ToolCall, Approval, and AuditLog.

An entity being listed here does not mean its integration contract or implementation already exists.

## 8. Agents and workflows

Planned agents:

| Agent | Responsibility |
|---|---|
| Commerce Manager | Coordinates cross-department work |
| Operations Agent | Orders, inventory, fulfilment, warehouses, shipping, returns, and suppliers |
| Finance Agent | Revenue, costs, margin, COD, settlements, cash flow, and P&L |
| Marketing Agent | Advertising, ROAS, CAC, CRM, and retention |
| Customer Experience Agent | Customer messages, complaints, tickets, and WhatsApp |
| Analytics Agent | KPIs, trends, anomalies, and forecasts |
| Growth Agent | Future cross-functional optimisation |

Only agents explicitly enabled for a company are available in its deployment.

Deterministic processes must be implemented as workflows, not delegated entirely to an LLM. For example, COD reconciliation should fetch shipments and settlements, match identifiers, calculate differences, and flag anomalies deterministically. An agent may then analyse the anomalies and write the report.

## 9. Memory and knowledge

Planned memory scopes:

- Company memory: company-wide policies and commercial assumptions.
- Department memory: department-specific operational patterns.
- Marketing memory: campaign patterns and learnings.
- User memory: report and interaction preferences.

The knowledge system will combine retrieved documents with structured data. Expected sources include SOPs, pricing policies, return policies, shipping rules, and supplier agreements. Vector search alone is not considered sufficient.

## 10. Current MVP

The approved MVP is intentionally narrow:

- One company: Fulfly.
- One agent: Operations Agent.
- Capabilities: read and report only.
- No operational writes.
- No complex approval workflow until report accuracy is proven.
- First use case: **Analyse today's operations**.

The target flow is:

```text
Read orders, shipping-related data, and available inventory data
  → Normalise provider data
  → Calculate deterministic operational metrics
  → Detect anomalies
  → Use the Operations Agent to interpret prioritised anomalies
  → Produce an actionable report
```

### FulFly Integration 001 contract boundary

The FulFly API documentation reviewed on 2026-10-05 supports the following candidate read-only operations:

- Authenticate using a currency-scoped JWT.
- List affiliate orders with page-based pagination.
- Read one order's details.
- Read one order's chronological status history.
- List visible product variants, although the documented response schema is incomplete.
- Read seller product variants with `availableStock` when a product ID is known.
- Read governorates, shipping cost, return cost, expected delivery duration, and areas.
- Receive best-effort order-status webhooks.

Important documented limitations:

- The documented order-list endpoint is restricted to the `Affiliate` role.
- There is no documented seller orders-list endpoint.
- There is no independent shipment, courier, warehouse, return, refund, COD, or settlement API.
- Order lists have no documented date, status, or incremental-update filters.
- Webhooks have no documented signature and failed deliveries are not retried.
- The inventory schema returned by the cross-product variant endpoint is not documented.
- Rate limits are not documented.

Therefore Integration 001 should initially promise **Affiliate Orders read-only analysis**. Inventory analysis remains conditional until a real response contract and account role are verified. Shipment, warehouse, return, refund, COD, and settlement domain entities must not be fabricated from incomplete fields.

## 11. Technology baseline

| Area | Technology |
|---|---|
| Backend | Python and FastAPI |
| Agent runtime | Agno through `AgnoAdapter` |
| Relational database | PostgreSQL |
| Vector storage | pgvector |
| Cache and queues | Redis |
| Frontend | Next.js |
| Object storage | S3-compatible storage |
| Deployment | Docker |
| Observability | OpenTelemetry, internal logs, and agent metrics |

The architecture must avoid locking the product to one model provider.

## 12. Deployment model

Supported deployment models:

- On-premise in the customer's environment.
- A dedicated VPS or isolated container environment hosted for the customer.

Every company deployment has physically isolated application, database, configuration, secrets, integrations, and knowledge. The design explicitly excludes a shared tenant layer, cross-tenant enforcement logic, and a central multi-company control plane.

## 13. Intended repository structure

```text
core-platform/
  agents/
  policy-engine/
  commerce-domain/
  tools/
  adapters/

skills/
  operations/
  finance/
  marketing/
  cx/
  management/

templates/
  company-config-template/

deployments/
  fulfly/
    config.yaml
    secrets/
    integrations/
    knowledge/
```

This is the architecture-level target structure. Use the repository tree as the authority for current physical paths.

## 14. Observability and audit

Technical and agent metrics should include:

- Runs, success rate, latency, and failures.
- Token usage and model cost.
- Tool calls and verification results.
- Approvals and human overrides.

Business-impact metrics should include:

- Revenue impact.
- Cost savings.
- Time saved.

Every auditable operation should identify the company, user, agent, tool, action, validated input, result, timestamp, approval state, and verification status. Sensitive values must be redacted.

## 15. Testing requirements

The expected test layers are:

- Unit tests.
- Integration and adapter contract tests.
- Agent and workflow tests.
- Permission and policy tests.
- Tool-security tests.
- Approval-state tests.
- Failure-recovery tests.
- Prompt-injection tests.
- End-to-end tests.

No feature is complete without explicit acceptance criteria.

## 16. Definition of Done

A feature is done only when:

- Its implementation is complete.
- Acceptance criteria are satisfied.
- Relevant tests pass.
- Permission and security behaviour are verified.
- Failure cases are handled.
- Logs and audit records are present.
- Documentation is updated.
- No known critical bug remains.

## 17. Current documentation state

This overview establishes the product and architecture baseline. The following subjects must remain reconciled with the repository's implementation documentation:

- `GETTING_STARTED.md`: installation and local startup.
- `ARCHITECTURE.md`: components, boundaries, and runtime diagrams.
- `CONFIGURATION.md`: supported settings and environment variables.
- `COMMERCE_DOMAIN.md`: entity contracts and invariants.
- `OPERATIONS_AGENT.md`: inputs, tools, analysis rules, and report contract.
- `TOOLS_CATALOG.md`: each tool's inputs, outputs, permissions, risks, and verification.
- `API_REFERENCE.md`: Agento's own HTTP API.
- `integrations/FULFLY_CONTRACT.md`: the verified FulFly API contract and field mapping.
- `SECURITY.md`: threat model and security requirements.
- `runbooks/TROUBLESHOOTING.md`: operational recovery procedures.
