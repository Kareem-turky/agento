# Tools Catalog

> **Status:** Proposed MVP tool contracts. Names may change when implementation code is introduced.

## Tool contract requirements

Every tool definition must specify:

- Capability name.
- Purpose and owner.
- Validated input schema.
- Output schema.
- Required caller and agent permissions.
- Risk level.
- External side effects.
- Timeout and retry policy.
- Verification method.
- Audit fields and redaction rules.

## MVP tools

### `orders.list`

- Purpose: List all accessible orders through controlled pagination.
- Permission: `orders.read`.
- Risk: `READ`.
- Inputs: provider, page/cursor abstraction, page size, optional local reporting window.
- Outputs: normalised order summaries plus coverage metadata.
- FulFly adapter: `GET /orders/affiliate-orders` with `page` and `recordsPerPage` headers.
- Verification: response schema, non-negative `totalOrders`, valid IDs/timestamps, pagination accounting.
- Redaction: phone/name excluded from logs.

### `orders.get`

- Purpose: Read one order in the caller's provider scope.
- Permission: `orders.read`.
- Risk: `READ`.
- Inputs: provider order ID.
- Outputs: normalised order detail and completeness flags.
- FulFly adapter: `GET /orders/order`, `orderId` header.
- Verification: returned order ID matches the requested ID.

### `orders.status_history`

- Purpose: Read chronological status events for one order.
- Permission: `order_status_history.read`.
- Risk: `READ`.
- Inputs: provider order ID.
- Outputs: ordered status events.
- FulFly adapter: `GET /orders/order-status-history`, `orderId` header.
- Verification: IDs are valid, timestamps parse, chronological order is validated or normalised explicitly.

### `shipping.regions.list`

- Purpose: Read governorate-level shipping reference data.
- Permission: `shipping_reference.read`.
- Risk: `READ`.
- Inputs: provider/currency context.
- Outputs: region IDs, names, costs, return costs, and expected duration.
- FulFly adapter: `GET /shipping/get-governments`.
- Verification: amount/currency consistency and valid region IDs.

### `shipping.areas.list`

- Purpose: Read areas for one governorate.
- Permission: `shipping_reference.read`.
- Risk: `READ`.
- Inputs: governorate ID.
- Outputs: area references.
- FulFly adapter: `GET /shipping/get-specific-governments-areas`, `govId` header.
- Verification: returned government reference matches the request.

### `inventory.variants.list`

- Purpose: Read visible variants for discovery.
- Permission: `inventory.read`.
- Risk: `READ`.
- FulFly adapter: `GET /products/all-product-variants`.
- Status: disabled for production analytics until the real response schema is contract-tested.

### `inventory.product_variants.list`

- Purpose: Read seller variants and available stock for one product.
- Permission: `inventory.read`.
- Risk: `READ`.
- Inputs: product ID and page.
- FulFly adapter: `GET /products/get-product-variants`.
- Constraint: Seller role only; 100 records per page.
- Status: conditional because Integration 001 may use Affiliate credentials and has no documented product-list source.

### `reports.operations.create`

- Purpose: Persist a deterministic operations report and its evidence references.
- Permission: `reports.create`.
- Risk: `LOW_RISK_WRITE` because it writes only internal report data.
- Inputs: validated workflow result, report version, coverage state.
- Outputs: immutable report ID and location.
- Verification: saved content hash and readable report metadata.

## Tools excluded from MVP

- Order creation or cancellation.
- Product or inventory modification.
- Variant image upload.
- Refund or return creation.
- Customer messaging.
- Advertising changes.
- Financial transfers.
- FulFly XLSX export, because it generates a public download URL and is unnecessary for the workflow.

## Provider error normalisation

Adapters convert errors into stable categories:

- `AUTHENTICATION_FAILED`
- `PERMISSION_DENIED`
- `INVALID_CONFIGURATION`
- `VALIDATION_FAILED`
- `NOT_FOUND_OR_OUT_OF_SCOPE`
- `RATE_LIMITED`
- `PROVIDER_UNAVAILABLE`
- `PROVIDER_CONTRACT_VIOLATION`
- `UNKNOWN_PROVIDER_ERROR`

The original status and redacted provider messages remain attached for diagnosis.

