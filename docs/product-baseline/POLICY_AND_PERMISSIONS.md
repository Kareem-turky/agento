# Policy and Permissions

> **Status:** Approved control model; implementation not yet verified.

## Decision layers

An action proceeds only when all layers allow it:

1. The authenticated user has the required company capability.
2. The selected agent manifest allows the capability.
3. The requested resource belongs to the current company deployment and provider scope.
4. Inputs pass schema and business validation.
5. The policy engine allows the action at its classified risk.
6. Any required approval is valid at execution time.

The most restrictive decision wins.

## MVP roles

The first implementation may begin with:

| Role | Capabilities |
|---|---|
| Operations Viewer | Run and read operations reports |
| Operations Analyst | Viewer capabilities plus order/detail evidence access |
| Company Admin | Configure users, integrations, and approved thresholds |
| System Service | Execute scheduled read-only workflows |

External provider roles such as FulFly Affiliate or Seller are integration attributes, not Agento user roles.

## Operations Agent manifest

```yaml
orders.read: true
order_status_history.read: true
shipping_reference.read: true
inventory.read: conditional
reports.create: true
orders.write: false
inventory.write: false
refund.create: false
customer_messages.send: false
```

## Policy decision record

Each decision records:

- Correlation and run IDs.
- Company, caller, and agent.
- Capability and resource scope.
- Policy version.
- Input classification and risk level.
- Decision: allow, deny, or approval required.
- Reason code safe for machine handling.
- Timestamp and approval reference where relevant.

Secrets and unnecessary PII are excluded.

## Approval model

Approval is not needed for the read-only MVP. Future writes use:

```text
Requested → Approved → Executed → Verified
          ↘ Rejected
          ↘ Expired
          ↘ Cancelled
```

An approval is bound to exact action type, resource, old value, new value, requester, and expiry. Material argument changes invalidate the approval.

## Default-deny rules

- Unknown capability: deny.
- Missing manifest entry: deny.
- Missing company/resource scope: deny.
- Unvalidated provider identifier: deny.
- Unavailable policy engine: deny external tool execution.
- Unknown write risk: classify as high risk and deny pending policy definition.
- Agent request to expand its own tools: deny.

## Separation of duties

High-risk actions should not be approved by the same identity that requested them. Production integration administration and business-action approval should be separate capabilities even if a small deployment initially assigns both to one named administrator.

## Testing requirements

- Allow and deny cases for every capability.
- User permission allowed but agent manifest denied.
- Agent manifest allowed but user permission denied.
- Cross-company/resource reference rejected.
- Approval expired or arguments changed.
- Policy unavailable fails closed.
- Tool list does not expose denied capabilities to the model.

