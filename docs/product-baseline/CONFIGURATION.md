# Configuration

> **Status:** Configuration ownership and proposed Integration 001 schema. Exact names must be reconciled with the repository's settings implementation and `.env.example` before adoption.

## Configuration layers

Configuration should be resolved in this order:

1. Safe platform defaults.
2. Company deployment configuration.
3. Environment-specific configuration.
4. Secret references resolved at runtime.

Secrets must never be stored in company YAML, source control, agent prompts, or knowledge files.

## Proposed company configuration

```yaml
company:
  id: fulfly
  display_name: Fulfly
  timezone: Africa/Cairo
  default_currency: EGP

agents:
  operations:
    enabled: true
    mode: read_report_only

integrations:
  fulfly:
    enabled: true
    base_url: https://apiv2.fulfly.net/
    account_role: affiliate
    currency_id_secret: secret://fulfly/currency-id
    email_secret: secret://fulfly/email
    password_secret: secret://fulfly/password

operations_report:
  business_day_timezone: Africa/Cairo
  terminal_statuses:
    - Complete
    - Returned
    - Cancelled
  anomaly_thresholds:
    waiting_hours: null
    packed_hours: null
    shipped_over_expected_days: null
```

Thresholds remain `null` until business owners approve their meaning. They must not be invented by the model.

## Configuration categories

### Platform

- Runtime environment.
- Public API origin.
- Database, Redis, and object-storage connections.
- Model-provider selection and approved models.
- Runtime and model timeouts.
- Telemetry destinations.

### Company

- Stable company identifier and display name.
- Business timezone and supported currencies.
- Enabled agents and features.
- Report schedule and recipients, when scheduling is implemented.

### Permissions and policies

- Role-to-capability assignments.
- Agent manifests.
- Risk classification overrides.
- Approval requirements and expiry.

### Integrations

- Provider base URL.
- Account role and capability flags.
- Secret references.
- Request timeout, retry budget, and concurrency limit.
- Webhook enablement and verification configuration.

### Business rules

- Status normalisation map.
- Terminal statuses.
- Anomaly thresholds.
- Report coverage and freshness limits.

## Validation rules

- Unknown configuration keys fail validation in production.
- Required fields fail startup before serving traffic.
- Secret references are resolved without printing their values.
- URLs must use HTTPS outside local development.
- Timezones use IANA names such as `Africa/Cairo`.
- Thresholds include explicit units.
- Provider role and currency must be verified during integration health checks.
- Production cannot start with demo credentials or permissive development policies.

## Reload behaviour

The future implementation must document which settings require restart. Security, permission, integration, or model changes should create an auditable configuration-change event. Silent hot reload is not acceptable for high-impact policy changes.
