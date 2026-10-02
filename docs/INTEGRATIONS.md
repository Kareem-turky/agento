# Integrations (provider-agnostic foundation)

> Status: **framework only.** This build installs **no** integration and connects to
> **no** external system. Nothing on this page means that a real provider is supported.

The Product is integration-ready at the framework level. It can describe which
integration types are installed, store a company's **connections** to them (metadata in
PostgreSQL, credentials in a separate secret store), test a connection through a
driver, and enable, disable or delete it. All of this goes through Product
authentication, governance and the existing audit trail. A generic Connections UI and
API exist.

What does **not** exist yet:

- **No real provider is connected or installed.** The production integration catalog is
  empty. There is no provider code, no provider credential and no provider network call.
- **The demo still uses mock data.** Business reads and the operational ticket write
  still run only against the deterministic `mock` business backend. The business backend
  registry is still exactly `{"mock"}`, and connections do not change it.
- **Source systems remain the source of truth.** The Product does not ingest, copy,
  synchronize or mirror business data (orders, shipments, inventory, customers…). It
  stores only connection metadata, never business records.
- **OAuth is not implemented.** An integration type that needs delegated authorization
  can be declared, but this build marks it *not connectable*.
- **No production business connectivity is claimed.** Staging and production still
  refuse to start, as described in
  [MVP release acceptance](MVP_RELEASE_ACCEPTANCE.md).

## Concepts

| Concept | Where | What it is |
| --- | --- | --- |
| `IntegrationDefinition` | `app/integration_management/definitions.py` | An installed integration type. It has a stable `integration_id`, a name, a category, a description and an auth mode (`none`, `credentials` or `delegated`). It declares its non-secret fields (`text`, `url`, `boolean`), its secret fields, and the capability identifiers it claims. |
| `IntegrationCategory` | same | `commerce`, `messaging`, `marketing`, `shipping`, `accounting`. **Classification only:** a category grants no permission and selects no behaviour. |
| `IntegrationCatalog` | `catalog.py` | An immutable, explicit allowlist of `(definition, driver)` pairs. There is no dynamic import, entry point, plugin directory, remote code or HTTP registration. `build_default_integration_catalog()` is **empty** in this build. Tests inject deterministic fakes. |
| `IntegrationConnectionDriver` | `drivers.py` | The lifecycle only: `validate_config`, `test_connection` and `aclose`. A driver has **no** business operations. Business reads and writes stay behind the existing integration contracts (for example `CommerceIntegration`). |
| `IntegrationConnection` | `connections.py` | One company's connection. It holds a UUID, `integration_id`, `display_name`, non-secret `config`, and the **names** of its configured secret fields. It also holds `enabled`, timestamps, and the last-known test result (`never_tested`, `success` or `failure`). A failed test carries a fixed, safe error classification. |
| `IntegrationSecretStore` | `secrets.py` | The secret boundary: `replace`, `field_names`, `read` (trusted server code only) and `delete`. |
| `FilesystemIntegrationSecretStore` | `filesystem_secrets.py` | The only secret store in this build (see below). |

Connection metadata is stored in `product.integration_connections`, created by Alembic
migration `0003_create_integration_connections`. There are no business-data tables.

## HTTP API

All routes are Product routes. They need a Product API key, and they are documented in
the OpenAPI schema under the `integrations` tag. A connection is addressed by the
`connection_id` query parameter.

| Method | Path | Permission | Purpose |
| --- | --- | --- | --- |
| GET | `/api/v1/integrations/catalog` | `integrations.read` | Installed integration types, grouped by category in order |
| GET | `/api/v1/integrations/connections` | `integrations.read` | Your company's connections (metadata) |
| POST | `/api/v1/integrations/connections` | `integrations.manage` | Create a connection (config plus initial credentials) |
| GET | `/api/v1/integrations/connection?connection_id=` | `integrations.read` | One connection (metadata) |
| PUT | `/api/v1/integrations/connection?connection_id=` | `integrations.manage` | Change the name and/or non-secret config. **Never touches credentials.** |
| PUT | `/api/v1/integrations/connection/credentials?connection_id=` | `integrations.manage` | Explicitly replace the complete credential set |
| POST | `/api/v1/integrations/connection/test?connection_id=` | `integrations.manage` | Test through the installed driver and record the result |
| POST | `/api/v1/integrations/connection/enable?connection_id=` | `integrations.manage` | Enable |
| POST | `/api/v1/integrations/connection/disable?connection_id=` | `integrations.manage` | Disable |
| DELETE | `/api/v1/integrations/connection?connection_id=` | `integrations.manage` | Delete the connection **and** its stored credentials |

- **Permissions:** `integrations.read` and `integrations.manage` are Product
  permissions granted to a Product API key (`APP_PRODUCT_API_KEYS`). Agents have
  neither, and Agent tools cannot reach these routes, so an Agent can never create,
  test or change a connection, nor its own credentials.
- **Error mapping:**

  | Status | Meaning |
  | --- | --- |
  | `401` | Unauthenticated |
  | `403` | Missing permission (the denial is audited) |
  | `404` | Unknown or another company's connection (indistinguishable) |
  | `422` | Invalid input, an integration type that is not installed, or one this build cannot connect (fail closed); the response never echoes a submitted value |
  | `409` | The operation did not complete; nothing changed |
  | `503` | Integration management, or its secret storage, is unavailable |

- **Audit:** every mutation is a governed action recorded in the existing audit trail
  (`product.audit_events`) through the existing `ExecutionCoordinator`. The actions are:
  - `integrations.connection.create`
  - `integrations.connection.update`
  - `integrations.connection.credentials.replace`
  - `integrations.connection.test`
  - `integrations.connection.enable`
  - `integrations.connection.disable`
  - `integrations.connection.delete`

  Audit events contain no secret values.

## Secret handling

Credential **values** cross the HTTP boundary in one direction only:

- They go only to the secret store. They are **never** written to PostgreSQL, returned
  by any route, logged, audited, placed in error details, or pre-filled in the UI.
- `GET` responses list only the *names* of configured secret fields
  (`configured_secret_fields`).
- Updating the config never clears or changes credentials. Replacing credentials is a
  separate, explicit request that sends the complete new set. The old set stays in place
  until the new one is written atomically. If the replacement fails, the old
  credentials are kept.
- Deleting a connection removes its secret material.
- Changing the config or replacing credentials resets the last-known test result to
  `never_tested`.

### Filesystem secret store (`APP_INTEGRATION_SECRETS_DIR`)

Set `APP_INTEGRATION_SECRETS_DIR` to an absolute path of an existing directory that is
owned by the API user and is **not** group- or world-writable (for example mode `0700`).
If it is unset, integration types that need credentials cannot be connected, and those
requests answer `503`. If it is set but unsafe, the API refuses to start.

- There is one file per connection, named only from the connection UUID
  (`<uuid>.json`). File names are never built from client input, which prevents path
  traversal and absolute-path injection.
- The root is checked on every operation and must be a real directory, not a symlink.
  Files are opened with `O_NOFOLLOW`, and a symlinked secret file is refused, so a
  symlink cannot escape the root.
- Writes are atomic: an exclusive temporary file at mode `0600`, `fsync`, rename, then a
  directory `fsync`. Deletes are deterministic and idempotent.
- Errors and logs never contain values or paths.

**Protection depends entirely on the security of that volume and host.** Values are
stored **unencrypted** on that filesystem. There is no encryption layer, no key
management and **no KMS/HSM support**. Restrict access to the volume, include it in your
own backup and encryption-at-rest policies, and never commit or share its contents. A
stronger store (for example an external secret manager) can implement the
`IntegrationSecretStore` protocol later.

## Connections UI

The Operations Console links to **Settings → Integrations** (`/settings/integrations`).
The page:

- reads the catalog through the Product API and groups it by category. With the empty
  catalog of this build it says **"No integrations are installed in this build."** It
  contains no hard-coded provider cards.
- lists connections with their enabled state and last-known test result and time.
- renders one generic form from the definition's field metadata: text, URL and boolean
  fields, plus write-only password inputs for secret fields. Secret inputs are never
  pre-filled and are cleared after each submission. All values are rendered as plain
  text; no provider-supplied HTML is ever interpreted.
- offers Test, Enable/Disable, Edit settings, Replace credentials and Delete (with
  confirmation). It never retries automatically.
- keeps the Product API key in page memory only, like the Operations Console. Leaving or
  reloading the page forgets it.

The web BFF forwards each of these routes to exactly one fixed Product method and path,
like the other Console routes.

## Adding an integration type (future work, reviewed code only)

1. Write an `IntegrationDefinition` and an `IntegrationConnectionDriver` in reviewed
   Product code. The driver handles connection lifecycle only.
2. Add them explicitly to the catalog built by `build_default_integration_catalog()`.
3. Any business read or write must still be a separate, reviewed adapter behind the
   existing integration contracts. It must pass the conformance harness and use the
   secure outbound transport (`app/integrations/http/`).

None of these steps are done for any provider in this build.
