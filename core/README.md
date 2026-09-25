# platform/

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

Note: when Python code is added, it must not be importable as a top-level package
named `platform`, which would shadow the Python standard library module of the same name.
