# Architecture

> **Status:** Approved architecture baseline; not verified against application code.

## System context

Agento is deployed once per company. A deployment serves that company's users and connects only to that company's integrations and data stores.

```text
Users and channels
  Web UI ─ WhatsApp ─ External API clients
                    │
                    ▼
              Agento API Gateway
                    │
        Authentication and permissions
                    │
                Policy Engine
                    │
            Commerce Orchestrator
             ┌──────┴──────┐
             ▼             ▼
          Workflows       Agents
             └──────┬──────┘
                    ▼
             Runtime interfaces
                    ▼
                AgnoAdapter
                    ▼
             Agno runtime + LLMs

Commerce Orchestrator → Controlled tools → Integration adapters → External systems
                               │
                               └→ Verification → Audit and metrics
```

## Components

### API Gateway

Receives channel requests, assigns correlation IDs, applies request-size and rate controls, authenticates the caller, and forwards a normalised request. It must not expose runtime-specific Agno objects.

### Authentication

Identifies the user or service principal within one company deployment. External integration credentials are separate from user authentication and remain in secret management.

### Permission Layer

Determines what the authenticated principal may request. It evaluates company roles and agent manifests before any tool is offered to an agent.

### Policy Engine

Evaluates the requested capability, resource scope, proposed arguments, risk level, and approval requirement. A denied policy decision cannot be overridden by an LLM.

### Commerce Orchestrator

Selects deterministic workflows and agents, prepares trusted context, coordinates tool execution, and produces the final business result. Deterministic calculations belong here or in workflows, not in free-form prompts.

### Agents

Interpret business questions, choose from an allowlisted set of read tools, analyse anomalies produced by workflows, and produce explanations or recommendations. Agents do not own provider authentication, validation, policy, or audit logic.

### Workflows

Implement repeatable processes with explicit inputs, ordered steps, retries, timeouts, and outputs. Examples include daily operations analysis and future COD reconciliation.

### Runtime Adapter

`AgnoAdapter` implements internal runtime interfaces. Core modules depend on those interfaces rather than importing Agno directly.

### Tools

Expose narrow business capabilities. A tool validates inputs, checks the effective policy decision, calls an adapter, verifies the result, emits metrics, and creates an audit event.

### Integration Adapters

Translate provider requests and responses into provider-neutral commerce contracts. Raw provider payloads may be retained in restricted storage for debugging, but must not become the public domain model.

### Data stores

- PostgreSQL: users, commerce snapshots, runs, policies, approvals, and audit metadata.
- pgvector: approved embeddings for knowledge retrieval.
- Redis: queues, short-lived locks, caches, and workflow coordination.
- S3-compatible storage: reports, approved knowledge files, and large artifacts.

## Dependency rules

Allowed:

```text
API → Application services → Commerce domain
Application services → Policy interfaces
Application services → Runtime interfaces
Tools → Integration interfaces
Adapter implementations → External SDKs/APIs
```

Forbidden:

```text
Frontend → Agno
Commerce domain → Agno
Commerce domain → FulFly/Shopify/Woo types
LLM prompt → secret store
Agent → provider API without a controlled tool
Integration adapter → policy bypass
```

## Read execution sequence

1. Authenticate the caller.
2. Authorise the requested business capability.
3. Create an `AgentRun` or `WorkflowRun` correlation context.
4. Restrict the available tools to the agent manifest.
5. Validate tool inputs.
6. Evaluate policy and risk.
7. Execute the provider-neutral tool through its adapter.
8. Validate and normalise the provider response.
9. Record audit metadata and metrics.
10. Generate a report with evidence references and data-quality warnings.

## Write execution sequence

Writes are outside the current MVP. When enabled, they add:

1. Capture current state and proposed state.
2. Require approval according to risk policy.
3. Revalidate the approval immediately before execution.
4. Execute with idempotency protection when supported.
5. Read the external system again to verify the new state.
6. Record the complete outcome and any compensation requirement.

## Isolation model

There is no shared application database or shared tenant control plane. Each company has isolated compute, database, Redis, storage namespace, secrets, integrations, configuration, and knowledge.

The `company_id` remains useful in records and audits for explicit ownership, but it is not a replacement for physical deployment isolation.

## Failure principles

- Fail closed on missing permission, policy, or credentials.
- Never present incomplete provider data as complete.
- Separate provider failures from agent reasoning failures.
- Preserve partial workflow state for safe retry.
- Do not retry validation, permission, or configuration errors.
- Bound retries for transient network and server failures.
- Reports must state freshness and coverage.

