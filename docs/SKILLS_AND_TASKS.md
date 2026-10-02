# Product Skills and Tasks (foundation v1)

> Status: **static, read-only Product metadata.** It describes what the existing
> Operations Agent can do. There is no Skill or Task editor, no user-created Skills or
> Tasks, and no Task executor yet.

## Five separate concepts

| Concept | Answers | Where |
| --- | --- | --- |
| **Agent** | WHO performs or reasons | `AgentDefinition` / `ProductAgentCatalog` (`app/agent_management/`); runtime Agent in `app/agents/operations.py` |
| **Skill** | WHAT capability the Agent possesses | `SkillDefinition` / `ProductSkillCatalog` (`app/agent_management/skills.py`) |
| **Task** | WHAT concrete Product job is requested | `TaskDefinition` / `ProductTaskCatalog` (`app/agent_management/tasks.py`) |
| **Tool** | HOW one atomic read or write is invoked | trusted tool code (`app/agents/operations_tools.py`), described by the Agent's `AgentManifest` |
| **Workflow** | HOW a deterministic multi-step process runs | Product Workflow `operations.daily_report` on the Workflow Platform (`app/workflow_management/`, Task 034), around `DailyOperationsWorkflow` (`app/workflows/operations_daily.py`); see [`WORKFLOWS.md`](WORKFLOWS.md) |

These stay separate:

- A Skill is not code, an Agno tool, a prompt, an MCP server, a provider adapter or a
  workflow.
- A Task is not a prompt, executable code, a workflow or a provider operation.

The relationship for the MVP use case "analyze operations today" is:

1. Task `operations.analyze_daily` uses Skill `operations.daily_analysis`.
2. That Skill binds the tool `get_daily_operations_report`.
3. The tool calls `DailyOperationsReportService`, which `DailyOperationsWorkflow`
   implements.

The Task describes the job, and the Workflow computes deterministically.

## Catalogs: Product-owned, explicit, immutable

`ProductSkillCatalog` and `ProductTaskCatalog` follow the same rules as the Agent and
Integration catalogs:

- They are reviewed static registrations, immutable after construction, and they reject
  duplicate ids.
- There is no dynamic import, entry point, directory scan, database or remote code, and no
  HTTP registration or mutation.
- There is **no persistence** (no migration, no table). Skills and Tasks are Product source
  code.

### Contents in this build

**Skills.** Each Skill covers exactly one existing capability. Every Operations tool is
covered once.

| Skill | Tools | Capability |
| --- | --- | --- |
| `operations.order_inspection` | `get_order`, `get_order_shipments` | `operations.analysis` |
| `operations.daily_analysis` | `get_daily_operations_report` | `operations.daily_report.explain` |
| `operations.ticket_escalation` | `create_operational_ticket` | `operations.ticket.request` |

**Tasks.**

| Task | Skill | Inputs | Declared envelope |
| --- | --- | --- | --- |
| `operations.inspect_order` | `operations.order_inspection` | `order_id` (UUID) | read-only, at most 4 tool calls |
| `operations.analyze_daily` | `operations.daily_analysis` | optional `business_date` (date) | read-only, at most 2 tool calls |
| `operations.escalate_issue` | `operations.ticket_escalation` | `title` (≤160), `description` (≤4000) | may write `operations.ticket.create`; explicit write intent required; at most 2 tool calls |

The Operations `AgentDefinition` declares these three Skill ids (`skill_ids`) and three
Task ids (`task_ids`). There are no placeholder Skills or Tasks for capabilities that don't
exist (inventory, returns, finance, messaging and so on).

## Tool binding: the manifest stays authoritative

- Skills reference **tool ids** only.
- The Agent's `AgentManifest` remains the single source of truth for each tool's
  read/write access, governed actions, the tool-call limit and safety properties.
- Tasks reference **Skill ids** only.
- Nothing copies the manifest.

## Acceptance criteria

`TaskAcceptanceCriterion` is a stable code plus a description. It is first-class,
**non-executable** Product metadata: never an expression, schema or validator. The
criteria mirror the existing runtime contract, for example:

- `operations.inspect_order`: `facts_from_tools`, `store_scope_preserved`,
  `no_fabricated_results`, `no_provider_payloads`.
- `operations.analyze_daily`: `daily_report_used`, `daily_report_authoritative`,
  `no_report_recalculation`, `no_invented_findings`, `business_date_not_guessed`,
  `coverage_respected`, `no_reconstruction_on_failure`.
- `operations.escalate_issue`: `explicit_write_intent_required`,
  `agent_cannot_authorize_writes`, `governance_authoritative`, `verified_write_only`,
  `unconfirmed_is_not_created`.

Task input fields use a fixed set of kinds: `text`, `uuid`, `date`, `boolean` and
`integer`. No JSON Schema, templates or code are executed.

## Task limits: contract metadata, not a security authority

`TaskLimits` declares:

- `max_tool_calls`;
- whether writes are possible;
- the allowed write actions;
- whether explicit trusted write intent is required.

There is **no Task executor in v1**, so these limits are Product contract metadata that
are validated for consistency. They are **not** a new security boundary. The authoritative
boundaries are unchanged:

```
trusted actor -> permission -> GovernanceGate -> policy -> trusted Agent run context
-> tool -> ExecutionCoordinator (writes) -> verification -> audit
```

A Task that declares a write does not grant that write. A Skill that contains a
write-capable tool does not grant permission to invoke it. Governance, execution and the
runtime never import Skills or Tasks.

## Cross-catalog validation (fails closed)

`ProductCapabilityGraph.build(agents, skills, tasks)` runs at composition time, before any
resource is acquired. An inconsistent build does not start. It verifies that:

- every Agent Skill and Task exists, and every Task's Skills exist;
- a Task supported by an Agent uses only Skills installed on that Agent;
- every Skill tool id is declared by the Agent's manifest, and a Skill's capabilities and
  requirements are the Agent's own;
- a Task's write envelope matches its Skills' tools:
  - allowed write actions must be governed actions of **write** tools in the manifest;
  - a read-only Task uses no write tool;
- a Task's `max_tool_calls` never exceeds the Agent's manifest tool-call limit (6).

Dangling or duplicate references raise `CapabilityGraphError`, which lists every problem.
Nothing is silently dropped.

## Product API (read-only, `agents.read`)

| Method | Path |
| --- | --- |
| GET | `/api/v1/skills/catalog` |
| GET | `/api/v1/skills/skill?skill_id=` |
| GET | `/api/v1/tasks/catalog` |
| GET | `/api/v1/tasks/task?task_id=` |

- The paths are fixed and exempted only as exact paths from AgentOS authentication, so
  Product authentication applies, never AgentOS.
- There are no create, update, delete, install or run endpoints.
- Agent responses (`/api/v1/agents*`) expose `skill_ids` and `task_ids` to resolve in these
  catalogs.
- Reading the catalogs makes no model, tool, provider, storage or network call.

**Settings → Agents** (`/settings/agents`) shows each Agent's Skills (with tool bindings
and a read/write indication) and Tasks (required Skills, declared envelope and acceptance
criteria), read-only. There are no controls.

## Independence

- **Provider-independent:** Skills and Tasks reference Product domain capabilities, never
  a named provider. Future adapters satisfy domain contracts underneath.
- **Runtime-independent:** the Skill and Task domain imports no `agno.*`. Agno is the
  pinned, self-hostable open-source Agent runtime; it is not the Product Skill or Task
  model. No Agno Cloud, Control Plane, hosted service, API key or paid feature is used or
  required.

## Not in this task

- There is no Task executor, task run endpoint, scheduler, queue or task history.
  Deterministic execution (state machine, retries, checkpoints, recovery) belongs to the
  Workflow Platform (Task 034, [`WORKFLOWS.md`](WORKFLOWS.md)). A Task only *names* the
  Product Workflow that performs it: `operations.analyze_daily` has
  `workflow_id = "operations.daily_report"`, validated (fail closed) against the Workflow
  catalog. `operations.inspect_order` and `operations.escalate_issue` are not
  Workflow-backed.
- There are no user-created or user-configured Skills or Tasks, and no LLM-driven task
  routing.
- Model-chosen Skill or Task ids are never trusted.
