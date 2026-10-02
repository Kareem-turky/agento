# Product Agents (management foundation)

> Status: **lifecycle management only.** One Product business Agent is installed: the
> **Operations Agent** (`operations`). This page does not describe a low-code Agent
> builder, and none exists.

Agents are first-class Product components. Their behaviour, meaning their instructions,
tools, governed actions and safety properties, is **trusted Product source code**. It
never comes from a provider, customer code, runtime imports or prompts typed into a UI.
Agent management lets an operator see which Product Agents are installed, read their
manifests, and turn each one on or off for this installation. Every change goes through
Product governance and the existing audit trail.

## Three separate concepts

| Concept | Where | What it is |
| --- | --- | --- |
| `AgentDefinition` | `app/agent_management/definitions.py` | An immutable description of an Agent **type** installed in this build. It has an `agent_id`, name, description, category, lifecycle, `default_enabled`, capabilities and an `AgentManifest`. It names no Python class, module or import path and holds no instructions, model or credentials. |
| `AgentConfiguration` | `app/agent_management/configuration.py`, table `product.agent_configurations` (migration `0004`) | An optional per-installation **override** of the definition default. It is deliberately narrow: `enabled`, plus company, agent id and timestamps. It has no prompt, model, provider, credential, tool selection or code reference. |
| Runtime Agent | `app/agents/operations.py` | The actual Agno Agent, built only by trusted Product composition code. Agent management never constructs, imports or runs an Agent. |

`ProductAgentCatalog` (`app/agent_management/catalog.py`) is the immutable, explicit list
of installed Product Agents:

- There is no discovery: no entry points, directory scans, import paths from
  configuration, database code or remote code.
- Nothing can register an Agent over HTTP.
- Duplicate ids are rejected.
- The default catalog contains exactly `operations`. The `generic-reasoning` model smoke
  Agent is runtime infrastructure, not a Product business Agent, so it is never listed.

## The Operations Agent manifest

The manifest is **descriptive** security and runtime metadata. It is **not** an
authorization authority: every tool call is still decided by
trusted actor → `GovernanceGate` → policy → `ExecutionCoordinator` → verification → audit.

| Tool | Access | Governed Product actions |
| --- | --- | --- |
| `get_order` | read | `operations.order.read` |
| `get_order_shipments` | read | `operations.shipments.read` |
| `get_daily_operations_report` | read | `operations.store.read`, `operations.orders.list`, `operations.shipments.list` |
| `create_operational_ticket` | write | `operations.ticket.create` (only when the run requested this write; the Product HTTP run is read-only) |

- **Tool-call limit:** 6.
- **Requirements:** Product domain capabilities only, never a provider: `commerce.*`
  reads, `operations.ticketing.write` and `model.default`.
- **Safety properties:**
  - trusted run context, store scoped, governed tools;
  - write intent required;
  - the model and tool output are untrusted;
  - the Product run is read-only;
  - no memory, knowledge or history;
  - not exposed through AgentOS.

An architecture test keeps the manifest equal to the trusted implementation (agent id,
tool names, tool-call limit, action names).

## Effective state

```
override stored?  yes -> configured = override.enabled   (source "override")
                  no  -> configured = definition default  (source "default")
disabled                          -> availability "disabled"     reason disabled_by_configuration
enabled, runtime not composed     -> availability "unavailable"  reason runtime_not_composed
enabled and runtime composed      -> availability "available"
```

"Enabled" alone never means "ready". For example, with `APP_BUSINESS_BACKEND=disabled`
the Operations runtime is not composed, so the Agent is enabled but unavailable. Reasons
are stable Product codes and never expose settings, models or credentials.

`operations` defaults to **enabled**. A fresh installation and the demo behave exactly as
before without any setup or seeded row. The migration inserts no rows.

## Enable / disable semantics

When the Operations Agent is disabled, `POST /api/v1/operations/runs`:

- answers `409 {"detail": "Operations Agent is disabled"}`;
- refuses **before** the Agent runs, so no model call, no tool call and no business side
  effect happen;
- hands nothing to a fallback Agent.

The gate (`AgentGatedOperationsRunService`) wraps the trusted runner at the Product run
boundary and reads the effective state for the trusted run company on every run. If the
configuration cannot be read, it **fails closed** with `503`. Re-enabling the Agent, or
resetting it to the default, restores normal behaviour immediately.

These are deliberately **not** gated, because they do not run the Agent:

- the deterministic daily report (`GET /api/v1/operations/reports/daily`);
- the explicit ticket write (`POST /api/v1/operations/tickets`);
- the command status lookup.

## HTTP API

These are Product routes with fixed paths; the agent id is passed as a query parameter.
They are distinct from AgentOS `/agents`, `/info` and session routes, which are never
proxied or used by the Product UI.

| Method | Path | Permission | Purpose |
| --- | --- | --- | --- |
| GET | `/api/v1/agents/catalog` | `agents.read` | Installed Product Agents: definitions and manifests |
| GET | `/api/v1/agents` | `agents.read` | Every Agent with its effective state |
| GET | `/api/v1/agents/agent?agent_id=` | `agents.read` | One Agent |
| POST | `/api/v1/agents/agent/enable?agent_id=` | `agents.manage` | Enable (stores an override) |
| POST | `/api/v1/agents/agent/disable?agent_id=` | `agents.manage` | Disable (stores an override) |
| DELETE | `/api/v1/agents/agent/configuration?agent_id=` | `agents.manage` | Reset to the Product default |

- **Errors:**

  | Status | Meaning |
  | --- | --- |
  | `401` | Unauthenticated |
  | `403` | Missing permission (mutation denials are audited) |
  | `404` | Unknown Agent |
  | `422` | Malformed id (never echoed) |
  | `409` | The operation did not complete |
  | `503` | Agent management is unavailable |

- **Audit:** enable, disable and reset are governed actions (`agents.agent.enable`,
  `agents.agent.disable`, `agents.configuration.reset`) recorded in `product.audit_events`
  by the existing `ExecutionCoordinator`. The audit records the action, actor, company,
  outcome and a verification code, and nothing else.
- **Agents never manage Agents.** No Agent has `agents.*`. On top of that, the
  Agent-management gate uses `HumanOperatorPermissionEvaluator`, which refuses every
  Agent-management action to a `system_agent` actor even if its permissions name one.
  The Operations Agent has no management tool, and `app/agents` never imports Agent
  management.

## Settings → Agents UI

`/settings/agents` (linked from the Operations Console and the Integrations page):

- lists the installed Product Agents with category, lifecycle, enabled state, effective
  availability (with a fixed explanation of the reason), capabilities and a read-only
  manifest summary;
- offers only **Enable**, **Disable** and **Reset to default**;
- has no prompt, instruction, model, API-key, tool, permission, JSON or code editor, and
  no "create Agent";
- keeps the Product API key in page memory only.

## Skills and Tasks (Task 033)

`AgentDefinition.skill_ids` and `AgentDefinition.task_ids` name the Product Skills the
Agent possesses and the Product Tasks it supports. They are resolved and validated, failing
closed, against the immutable Skill and Task catalogs; see
[`SKILLS_AND_TASKS.md`](SKILLS_AND_TASKS.md).

- Skills bind to tool ids of this manifest.
- Task limits never exceed this manifest's tool-call limit.
- Neither grants anything.

## Not in this task

These are not implemented: a Task executor, knowledge, company operating
context, Agent-specific model policy, controlled tool bindings and new business Agents.
The domain keeps these separate (definition, configuration, runtime) so they can be added
later without rewriting Agent management. Agent-management routes are not yet part of
Product observability; they are audited.
