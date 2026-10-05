# Operations Agent and Integration 001

> **Status:** "Existing behaviour" is `VERIFIED_CURRENT_PRODUCT` (from
> `apps/api/app/agents/operations*.py`, [`../AGENTS.md`](../AGENTS.md) and
> [`../EMPLOYEE_CHAT.md`](../EMPLOYEE_CHAT.md)). "Integration 001 effects" is
> `OPEN_ARCHITECTURE_DECISION` where it depends on the gate. This document defines no
> new Agent and no new manifest.

## Existing behaviour

The Operations Agent (`operations`) already exists. It is the only installed Product
business Agent. Its manifest, tools and safety properties are defined by trusted Product
code and kept equal to the implementation by an architecture test; the canonical
description is [`../AGENTS.md`](../AGENTS.md).

| Tool | Access | Governed actions |
|---|---|---|
| `get_order` | read | `operations.order.read` |
| `get_order_shipments` | read | `operations.shipments.read` |
| `get_daily_operations_report` | read | `operations.store.read`, `operations.orders.list`, `operations.shipments.list` |
| `create_operational_ticket` | write (internal ticket) | `operations.ticket.create`, only when the run requested that write |

- Tool-call limit 6; no memory, knowledge or history; not exposed through AgentOS.
- `POST /api/v1/operations/runs` runs it **read-only**: no write is requested, so the
  ticket tool always refuses.
- **Employee Chat** uses the same Agent identity with the three read tools plus
  `propose_operational_ticket`, which writes nothing. A ticket is created only after
  a separate human confirmation through the governed WriteCommand path. See
  [`../EMPLOYEE_CHAT.md`](../EMPLOYEE_CHAT.md).
- The Agent reads business data only through `CommerceIntegration`; it never sees a
  provider, transport, credential or integration connection.

## Integration 001 effects

If, after the gate closes, the business backend for a deployment were a FulFly-backed
`CommerceIntegration`:

- **No new tool, action, permission or manifest entry.** The Agent would keep exactly
  the tools above. FulFly endpoints do not become tools (see
  [TOOLS_CATALOG.md](TOOLS_CATALOG.md)).
- **No provider write.** FulFly Integration 001 introduces no external/provider write
  capability. The internal ticket tool and the Employee Chat proposal flow remain
  unchanged; they write to the Product's ticketing contract, not to FulFly.
- `get_order` would return a FulFly-sourced canonical `Order` only if the order mapping
  is complete (gates 1, 2, 6).
- `get_order_shipments` has no FulFly source. With no shipment capability it must
  surface "unavailable", never an empty list presented as fact, and never shipments
  derived from order statuses (gate 3).
- `get_daily_operations_report` inherits the workflow's shipment and pagination
  blockers (gates 3, 4). See
  [workflows/DAILY_OPERATIONS_ANALYSIS.md](workflows/DAILY_OPERATIONS_ANALYSIS.md).
- Coverage: until FulFly confirms stable paging, any FulFly-backed order data is
  `unverified`/`partial`. The Agent must not describe it as the complete set of the
  day's orders; how that is surfaced depends on the gate 4 Core decision.

## Data-handling expectations (unchanged principles)

- Provider text (customer names, notes, product titles) stays untrusted data and cannot
  change tools, policy or instructions.
- FulFly customer PII (names, phones, addresses) is not part of the canonical `Order`
  and therefore never reaches the Agent.
- `deliveredIn`, `isWaitingForStock`, `direction` and payment fields are not mapped, so
  the Agent cannot reason from them.
