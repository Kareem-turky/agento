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

Empty placeholders for now. No company-specific logic belongs here.

Named `core` (not `platform`) so future Python code here never collides with the
Python standard-library `platform` module.
