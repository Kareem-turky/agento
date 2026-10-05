# Policy and permissions

> **Status:** `VERIFIED_CURRENT_PRODUCT` (from `apps/api/app/operations/actions.py`,
> `apps/api/app/workflows/operations_daily.py`, [`../AGENTS.md`](../AGENTS.md),
> [`../INTEGRATIONS.md`](../INTEGRATIONS.md), [`../APPROVALS.md`](../APPROVALS.md)) unless
> a section says otherwise. Integration 001 adds **no** permission and **no** action.

## How decisions are made today

Every governed action goes through: trusted actor → `GovernanceGate` → policy →
`ExecutionCoordinator` → verification → audit. Permissions are Product permissions
granted to a Product API key. Agent manifests are descriptive metadata, not an
authorization authority. See [`../AGENTS.md`](../AGENTS.md).

## Operations actions (current)

| Action | Risk | Required permission | Scope | Used by |
|---|---|---|---|---|
| `operations.order.read` | `READ` | `orders.read` | store | Agent tool `get_order` |
| `operations.shipments.read` | `READ` | `shipments.read` | store | Agent tool `get_order_shipments` |
| `operations.store.read` | `READ` | `stores.read` | store | Daily workflow preflight |
| `operations.orders.list` | `READ` | `orders.read` | store | Daily workflow preflight |
| `operations.shipments.list` | `READ` | `shipments.read` | store | Daily workflow preflight |
| `operations.ticket.create` | `LOW_RISK_WRITE` | `tickets.create` | store | `POST /api/v1/operations/tickets`; Employee Chat confirmation; Agent tool `create_operational_ticket` only when a run requested that write |

The daily report (`GET /api/v1/operations/reports/daily`, and the Agent tool
`get_daily_operations_report`) requires **all** of `operations.store.read`,
`operations.orders.list` and `operations.shipments.list` to be allowed, i.e. the
permissions `stores.read`, `orders.read` and `shipments.read`.

There is **no** `operations.report.read`, `operations.report.run`, `reports.create`,
`order_status_history.read`, `shipping_reference.read`, `inventory.read` or
`integrations.health.read` permission. Earlier drafts of this package used those
names; they were never part of the Product.

## Other permissions touched by Integration 001

| Permission | Purpose |
|---|---|
| `integrations.read` | Read the integration catalog and connections |
| `integrations.manage` | Create, update, replace credentials, test, enable, disable, delete connections |

Agents have neither; Agent tools cannot reach integration management.

## Provider roles are not Agento roles

FulFly account types (Seller, Affiliate, Moderator, Buyer) and FulFly capability flags
are attributes of the provider credential, not Agento permissions. They decide which
FulFly endpoints the adapter can call; they never grant anything inside Agento.

## Writes and approvals

- FulFly Integration 001 introduces no external/provider write capability.
- Existing Agento internal/governed ticket and approval capabilities remain unchanged:
  - `operations.ticket.create` is a governed internal write through the ticketing
    contract (WriteCommand, `Idempotency-Key`, verification, audit).
  - Employee Chat can only **propose** that ticket; a separate human confirmation runs
    the governed WriteCommand path.
  - A durable approval foundation exists (`requested → approved | rejected | expired |
    cancelled`, permissions `approvals.read`, `approvals.decide`, `approvals.cancel`).
    No real business action currently requires an approval.

## Integration 001 effect

None on policy. A FulFly adapter, if built, would be reached only through the
existing actions above. Any new permission or action would be a `PROPOSED_FUTURE`
Core change and requires its own reviewed task.
