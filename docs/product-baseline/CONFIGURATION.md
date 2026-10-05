# Configuration for Integration 001

> **Status:** "Existing mechanisms" is `VERIFIED_CURRENT_PRODUCT` (from
> [`../INTEGRATIONS.md`](../INTEGRATIONS.md), `apps/api/app/integration_management/`,
> `apps/api/app/composition/`). The FulFly connection model is
> `OPEN_ARCHITECTURE_DECISION` until gate 5 closes. Nothing here is implemented.

## Existing mechanisms (authoritative)

There is no separate `integrations.fulfly` YAML file or company YAML that configures
providers, and this package does not introduce one. Integration configuration uses the
existing mechanisms:

| Mechanism | What it holds | Where values live |
|---|---|---|
| `IntegrationDefinition` | Declared non-secret fields (`text`, `url`, `boolean`) and secret field names | Reviewed Product code |
| `IntegrationConnection` | One connection's non-secret `config`, `enabled`, last test result, names of configured secret fields | PostgreSQL `product.integration_connections` |
| `IntegrationSecretStore` | Credential values | `APP_INTEGRATION_SECRETS_DIR` (filesystem store, one file per connection, unencrypted on that volume) |
| `APP_BUSINESS_BACKEND` + `BusinessBackendRegistry` | Which business backend the Product composes | Deployment configuration; only `mock` is registered |
| `BusinessBackendInputs` | Startup config/secret inputs declared by a backend registration | `APP_BACKEND_CONFIG_DIR` / `APP_BACKEND_SECRETS_DIR` |

Connections manage lifecycle only and do not change the business backend. Which of the
last two paths would feed a FulFly business adapter is gate 5.

`IntegrationDefinition` refuses a non-secret field whose name looks like a credential
(`password`, `secret`, `token`, `api_key`, …), so credentials cannot be placed in
`config` by mistake.

## Proposed FulFly connection model (`OPEN_ARCHITECTURE_DECISION`)

| Field | Kind | Placement | Notes |
|---|---|---|---|
| `base_url` | `url` | config | `https://apiv2.fulfly.net/`; HTTPS only |
| `currency_id` | `text` | config | Opaque FulFly currency id. Not a credential (it is sent in a header on every request, including public endpoints), so it is likely config, not secret. **Verify** with FulFly before treating it as non-sensitive. |
| `account_role` | `text` | config | Expected FulFly role (Affiliate for the documented order list). Validated by `test_connection`, never trusted as a grant. |
| `email` | secret | `IntegrationSecretStore` | Never in PostgreSQL, responses, logs or audit. |
| `password` | secret | `IntegrationSecretStore` | Same. |

### JWT handling (`APPROVED_INTEGRATION_DECISION`)

The FulFly JWT is runtime-derived state, not configuration:

- derived from the stored credentials by logging in;
- held only in a short-lived in-process cache (never longer than its 12-hour validity);
- never stored as connection metadata or in any database;
- never returned by any route;
- never logged;
- never audited;
- refreshed by re-authenticating after an appropriate authentication failure (for
  example a `401`), with a bounded number of attempts.

## Not configuration in Integration 001

- **Business thresholds, terminal-status lists and anomaly rules.** The daily report has
  a fixed, Product-owned rule set; Integration 001 adds no configurable rules.
- **Status mapping.** Part of the reviewed adapter code, not runtime configuration.
- **Store timezone and currency.** Come from the `Store` returned by `get_store`; how a
  FulFly-backed Store is anchored is gate 2.

## Dependencies

- PostgreSQL is the only required data store.
- Redis is present in the local Compose file but is **not** a readiness dependency, and
  Integration 001 must not require it.
- No object storage (S3 or similar) is used or required.
