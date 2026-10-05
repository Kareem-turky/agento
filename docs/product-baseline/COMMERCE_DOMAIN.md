# Commerce Domain

> **Status:** Provider-neutral domain baseline for the read-only MVP.

## Design principles

- Domain objects use Agento terminology, not provider response types.
- Provider IDs are retained as external references.
- Raw status and raw payload version are retained for traceability.
- Missing provider data remains missing; it is never inferred by an LLM.
- Money always carries currency.
- Timestamps carry timezone or are normalised to UTC.
- Customer PII is separated from operational reporting fields.
- A provider field is not promoted to a domain entity without sufficient semantics.

## MVP aggregates

### Company

Represents the isolated deployment owner.

Required fields:

- `id`
- `display_name`
- `timezone`
- `default_currency`

### Store

Represents an operational sales source when the provider exposes one. FulFly's documented API does not guarantee a store in read responses, so this relationship is optional in Integration 001.

### CommerceOrder

Provider-neutral order snapshot.

| Field | Meaning |
|---|---|
| `id` | Internal immutable identifier |
| `company_id` | Owning company |
| `provider` | For example `fulfly` |
| `provider_order_id` | Provider `_id` |
| `external_reference` | Provider-visible reference such as FulFly `track` |
| `provider_status` | Exact unmodified provider status |
| `normalised_status` | Agento status when mapping is approved |
| `direction` | Forward, exchange, or provider-specific unknown |
| `created_at` | Provider creation time |
| `currency` | Currency identifier/code |
| `net_amount` | Net amount when explicitly provided |
| `shipping_amount` | Explicit shipping amount |
| `provider_total_cost` | Provider field retained without redefining its semantics |
| `payment_status_raw` | Provider payment status |
| `inventory_hold` | Explicit waiting-for-stock flag |
| `customer_snapshot` | Restricted PII snapshot |
| `shipping_region` | Provider region reference |
| `items` | Order item snapshots |
| `source_updated_at` | Provider update time when available |
| `ingested_at` | Agento ingestion time |
| `raw_contract_version` | Mapping version used |

### OrderItemSnapshot

Only populate fields present in the provider payload. Typical fields are provider variant/product identifiers, title, code/SKU, quantity, and unit/line price. FulFly's documented order-detail example does not specify all these fields, so the MVP mapper must accept partial items.

### OrderStatusEvent

| Field | Meaning |
|---|---|
| `provider_event_id` | Provider history-entry ID |
| `provider_order_id` | Related order |
| `provider_status` | Exact status |
| `occurred_at` | Provider `createdAt` |
| `ingested_at` | Agento ingestion time |

FulFly returns these events oldest-first.

### InventorySnapshot

| Field | Meaning |
|---|---|
| `provider_variant_id` | Variant ID |
| `provider_product_id` | Product ID when supplied |
| `available_quantity` | Explicit provider `availableStock` only |
| `price` | Explicit variant price with currency |
| `is_approved` | Provider approval flag |
| `observed_at` | Ingestion time unless provider supplies a stock timestamp |

Absence of a variant is not equivalent to zero inventory.

### ShippingRegionReference

Represents reference data, not a shipment:

- Provider governorate ID and name.
- Currency.
- Shipping cost.
- Return cost.
- Expected delivery duration in provider-documented units.
- Optional area IDs and names.

### AgentRun and ToolCall

Record an agent/workflow invocation and its controlled tool activity. They must include correlation identifiers, caller, timing, status, selected capability, redacted arguments, result metadata, error classification, and verification state.

### AuditLog

Immutable security and business audit event. Audit logs must not contain authentication secrets or unmasked customer PII.

## Status mapping

FulFly statuses currently documented:

```text
New
Confirmed
Waiting
Printed
Packed
Shipped
Delivered
Complete
Return Request
Returned
Cancelled
```

Recommended initial normalisation:

| FulFly status | Agento status |
|---|---|
| New | `NEW` |
| Confirmed | `CONFIRMED` |
| Waiting | `ON_HOLD` |
| Printed | `FULFILMENT_PROCESSING` |
| Packed | `READY_TO_SHIP` |
| Shipped | `SHIPPED` |
| Delivered | `DELIVERED` |
| Complete | `COMPLETED` |
| Return Request | `RETURN_REQUESTED` |
| Returned | `RETURNED` |
| Cancelled | `CANCELLED` |

The raw status remains authoritative. Business owners must approve whether `Delivered` and `Complete` have distinct operational meanings before KPI definitions use one or both as successful delivery.

## Entities intentionally not created in Integration 001

- `Shipment`: no documented shipment resource or tracking-event API.
- `Courier`: no documented courier API.
- `Warehouse`: only a warehouse ID appears in a product-write input.
- `Return`: only order statuses and regional return cost are available.
- `Refund`: no refund contract.
- `COD` and `Settlement`: no explicit COD or settlement contract.

These entities remain in the long-term domain but cannot be populated from assumptions.

## Money rules

- Store amount and currency together.
- Do not add amounts from different currencies.
- Do not interpret FulFly `totalCost`, `netPrice`, or `totalPayment.aff` beyond their documented names until FulFly confirms their accounting definitions.
- Preserve raw values for later reconciliation.
- Decimal arithmetic is required; binary floating-point is not acceptable for financial calculations.

## Data freshness and provenance

Every snapshot used in a report must carry provider, ingestion time, query coverage, and any pagination or partial-failure warning. Reports must not describe stale or partial data as current or complete.

