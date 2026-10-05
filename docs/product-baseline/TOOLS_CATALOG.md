# Tools and provider operations

> **Status:** classification per row. Current tools and actions are
> `VERIFIED_CURRENT_PRODUCT`. Provider operations are `VERIFIED_PROVIDER_DOC` for their
> existence and `OPEN_ARCHITECTURE_DECISION` for their use.

## Principle

Provider endpoints do **not** become Agent tools. The Agent surface stays the minimal,
existing, provider-neutral set. A FulFly endpoint can only be used **inside** a reviewed
`CommerceIntegration` adapter (or connection driver) to implement an existing contract
method.

## Classification

| Entry | Class | Notes |
|---|---|---|
| `get_order` | **current tool** | → `operations.order.read` → `CommerceIntegration.get_order` |
| `get_order_shipments` | **current tool** | → `operations.shipments.read` → `list_shipments(order_id)`; no FulFly source |
| `get_daily_operations_report` | **current tool** | → daily workflow |
| `create_operational_ticket` | **current tool** (internal write) | Ticketing contract, not FulFly; refused unless the run requested the write |
| `propose_operational_ticket` | **current tool** (Employee Chat only) | No side effect |
| `operations.store.read`, `operations.orders.list`, `operations.shipments.list` | **current actions** | Daily workflow preflight |
| `POST /auth/login` | **adapter-internal** | Credential → JWT; also the basis of a driver `test_connection` |
| `GET /orders/affiliate-orders` | **adapter-internal** | Would back `list_orders`; pagination blocker |
| `GET /orders/order` | **adapter-internal** | Would back `get_order` and item completion for `list_orders` |
| `GET /orders/order-status-history` | **provider operation not exposed to the agent** | No contract method needs it; adapter-internal at most |
| `GET /shipping/get-governments` | **provider operation not exposed to the agent** | Reference data, no Core target; `deliveredIn` unit unknown |
| `GET /shipping/get-specific-governments-areas` | **provider operation not exposed to the agent** | Reference data, no Core target |
| `GET /products/all-product-variants` | **provider operation not exposed to the agent** | Schema undocumented; gate 7 |
| `GET /products/get-product-variants` | **provider operation not exposed to the agent** | Seller-only; gate 7 |
| `GET /categories/get-all-public-categories` | **provider operation not exposed to the agent** | No Core target |
| FulFly order create/cancel, product create, image upload, XLSX export | **provider operation not exposed to the agent**; never called | FulFly Integration 001 introduces no external/provider write capability |
| `orders.status_history`, `shipping.regions.list`, `shipping.areas.list`, `inventory.variants.list`, `inventory.product_variants.list` tools | **proposed future** — not recommended | Earlier drafts proposed these as Agent tools. They would widen the Agent surface with provider-shaped tools and are not part of Integration 001. |
| `reports.operations.create` | **proposed future** — not recommended | Reports are not persisted today. Report persistence would be a separate Core decision, not a tool. |

## Error normalisation

Adapters translate provider and transport errors into the existing contract errors only
(`apps/api/app/integrations/commerce/errors.py`):

| Situation | Contract error |
|---|---|
| Unknown canonical id | `IntegrationNotFoundError` |
| Provider unreachable, timeout, `5xx`, auth failure | `IntegrationUnavailableError` |
| Response cannot be mapped (missing items, bad money, naive timestamp, unknown shape) | `IntegrationDataError` |

No new error categories are introduced. Provider error text (`errArr[].msg`) is never
forwarded to Product clients or the model.
