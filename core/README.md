# core/

Generic core product layers built **around** Agno (Agno itself is an external dependency).

| Directory | Intended responsibility (future tasks) |
|---|---|
| `agents/` | Agent definitions composed from Agno primitives |
| `teams/` | Multi-agent team compositions |
| `workflows/` | Deterministic, multi-step processes |
| `tools/` | Tools exposed to agents |
| `actions/` | Controlled side-effecting operations |
| `policies/` | Policy evaluation |
| `permissions/` | Permission model |
| `approvals/` | Human-in-the-loop approvals |
| `verification/` | Verification of agent outputs/actions |
| `audit/` | Audit trail |

The first of these layers are implemented in the API package:
`apps/api/app/governance/` holds actions (catalog and intents), permissions and the
baseline policy, and `apps/api/app/execution/` holds governed action execution,
verification and audit events (no approval workflow yet), and
`apps/api/app/operations/` holds the first governed business action
(`operations.ticket.create`) and its read actions; `apps/api/app/agents/operations*.py`
is the Operations Agent that uses them through governed tools. The directories here remain empty placeholders. No company-specific logic belongs here.

Named `core` (not `platform`) so future Python code here never collides with the
Python standard-library `platform` module.
