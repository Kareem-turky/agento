#!/usr/bin/env bash
# CI-only backup / restore drill of the Agento deployment package (Task 039).
#
#   .github/scripts/backup-restore-drill.sh <api-image> <web-image>
#
# Runs ONLY against a DISPOSABLE Compose project and volume (never an operator's or a
# developer's real installation) and always removes them, even on failure:
#
#   migrate -> start -> create known Product state through the Product API (disable the
#   operations Agent) -> ONLINE backup (deployments/template/ops/backup.sh) -> backup file
#   checks (custom format, 0600, checksum, no partial, no overwrite, failure leaves
#   nothing) -> restore refusals (live / non-empty target) -> destroy the volume -> fresh
#   EMPTY database -> bad checksum refused before any change -> restore-into-empty.sh
#   (restore + explicit migration) -> start -> ready -> the known state survived.
#
# Every credential is a throwaway CI-only value generated here; none is printed. No model,
# provider or external integration call is made.
set -euo pipefail

API_IMAGE="${1:?usage: backup-restore-drill.sh <api-image> <web-image>}"
WEB_IMAGE="${2:?usage: backup-restore-drill.sh <api-image> <web-image>}"
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
TEMPLATE="$ROOT/deployments/template"
OPS="$TEMPLATE/ops"
PROJECT="cap-restore-drill"
WEB_PORT="13090"
WEB="http://127.0.0.1:$WEB_PORT"
WORK="$(mktemp -d)"
ENV_FILE="$WORK/drill.env"
BACKUPS="$WORK/backups"

RUN_ID="$(od -An -N8 -tx1 /dev/urandom | tr -d ' \n')"
PG_PASSWORD="ciOnlyDrillPg${RUN_ID}"
OS_KEY="ci-only-drill-agentos-${RUN_ID}-000000000000"
PRODUCT_KEY="ci-only-drill-product-key-${RUN_ID}-00000000"
PRODUCT_KEY_SHA="$(printf '%s' "$PRODUCT_KEY" | sha256sum | cut -d' ' -f1)"
PRINCIPALS="[{\"key_id\":\"ci-drill\",\"key_sha256\":\"$PRODUCT_KEY_SHA\",\"actor_id\":\"ci-drill-operator\",\"role_ids\":[\"operations\"],\"permissions\":[\"agents.read\",\"agents.manage\",\"system.read\"],\"store_ids\":[]}]"

compose() {
  docker compose -p "$PROJECT" -f "$TEMPLATE/compose.yaml" --env-file "$ENV_FILE" "$@"
}

cleanup() {
  status=$?
  if [[ "$status" != 0 ]]; then
    compose ps -a || true
    compose logs --no-color --tail 80 || true
  fi
  compose down -v --remove-orphans >/dev/null 2>&1 || true
  rm -rf "$WORK"
}
trap cleanup EXIT

step() { printf '\n== %s\n' "$*"; }
fail() { printf 'FAIL: %s\n' "$*" >&2; exit 1; }
status_of() { curl -s -o /dev/null -w '%{http_code}' "$@"; }
key() { printf 'Authorization: Bearer %s' "$PRODUCT_KEY"; }
psql_value() {
  compose exec -T postgres psql -U platform -d platform -v ON_ERROR_STOP=1 -tAc "$1"
}
user_schemas() {
  psql_value "SELECT count(*) FROM pg_namespace WHERE nspname NOT LIKE 'pg\\_%'
              AND nspname NOT IN ('information_schema', 'public')"
}
agent_enabled() {
  curl -sf -H "$(key)" "$WEB/api/product/agent-management/agent?agent_id=operations" \
    | python3 -c 'import json, sys; print(json.load(sys.stdin)["agent"]["state"]["enabled"])'
}

cat > "$ENV_FILE" <<EOF
API_IMAGE=$API_IMAGE
WEB_IMAGE=$WEB_IMAGE
PRODUCT_WEB_PORT=$WEB_PORT
POSTGRES_DB=platform
POSTGRES_USER=platform
POSTGRES_PASSWORD=$PG_PASSWORD
APP_ENVIRONMENT=test
APP_LOG_LEVEL=INFO
APP_OTEL_EXPORT_MODE=disabled
APP_OTEL_EXPORT_ENDPOINT=
APP_PRODUCT_AUTH_MODE=api_key
APP_COMPANY_ID=ci-drill-company
APP_PRODUCT_API_KEYS=$PRINCIPALS
OS_SECURITY_KEY=$OS_KEY
APP_BUSINESS_BACKEND=disabled
APP_BACKEND_CONFIG_DIR=
APP_BACKEND_SECRETS_DIR=
APP_DEFAULT_MODEL_PROVIDER=disabled
APP_DEFAULT_MODEL_ID=
OPENAI_API_KEY=
ANTHROPIC_API_KEY=
EOF
chmod 600 "$ENV_FILE"
mkdir -m 700 "$BACKUPS"

step "disposable installation: migrate, start, ready"
compose up -d --wait --wait-timeout 300 web
[[ "$(status_of "$WEB/api/product/health/ready")" == 200 ]] || fail "installation not ready"

step "known Product state through the Product API: disable the operations Agent"
[[ "$(agent_enabled)" == True ]] || fail "operations Agent not enabled by default"
[[ "$(status_of -X POST -H "$(key)" \
      "$WEB/api/product/agent-management/agent/disable?agent_id=operations")" == 200 ]] \
  || fail "could not disable the operations Agent"
[[ "$(agent_enabled)" == False ]] || fail "operations Agent still enabled"
rows="$(psql_value "SELECT count(*) FROM product.agent_configurations")"
[[ "$rows" -ge 1 ]] || fail "no Agent configuration row was persisted"

step "online backup while the Product is serving"
"$OPS/backup.sh" --project "$PROJECT" --env-file "$ENV_FILE" "$BACKUPS/agento.dump"
dump="$BACKUPS/agento.dump"
[[ -s "$dump" && "$(head -c 5 "$dump")" == PGDMP ]] || fail "not a non-empty custom-format dump"
[[ "$(stat -c %a "$dump")" == 600 && "$(stat -c %a "$dump.sha256")" == 600 ]] \
  || fail "backup files are not 0600"
(cd "$BACKUPS" && sha256sum -c --quiet agento.dump.sha256) || fail "checksum does not verify"
[[ -z "$(find "$BACKUPS" -name '.agento-backup.*')" ]] || fail "a temporary file was left behind"
# The whole database: the Product schema AND the Agno runtime schema are in the dump.
listing="$(compose exec -T postgres pg_restore --list < "$dump")"
grep -q ' SCHEMA - product ' <<< "$listing" || fail "product schema missing from the dump"
grep -q ' SCHEMA - agno_runtime ' <<< "$listing" || fail "Agno runtime schema missing from the dump"
grep -q ' TABLE DATA product agent_configurations ' <<< "$listing" || fail "Agent configuration data missing"
grep -q ' TABLE DATA product audit_events ' <<< "$listing" || fail "audit data missing"
for secret in "$PG_PASSWORD" "$OS_KEY" "$PRODUCT_KEY"; do
  grep -aqF "$secret" "$dump" && fail "the backup contains a credential"
done
echo "backup ok: $(stat -c %s "$dump") bytes, 0600, checksum verified, product + agno_runtime"

step "a backup never overwrites an existing one"
before="$(sha256sum "$dump")"
if "$OPS/backup.sh" --project "$PROJECT" --env-file "$ENV_FILE" "$dump" 2>/dev/null; then
  fail "backup overwrote an existing file"
fi
[[ "$(sha256sum "$dump")" == "$before" ]] || fail "existing backup changed"

step "a failed backup leaves no file behind"
if "$OPS/backup.sh" --project "$PROJECT-absent" --env-file "$ENV_FILE" "$BACKUPS/failed.dump" 2>/dev/null; then
  fail "a backup of a missing installation succeeded"
fi
[[ ! -e "$BACKUPS/failed.dump" && ! -e "$BACKUPS/failed.dump.sha256" ]] || fail "partial backup published"
[[ -z "$(find "$BACKUPS" -name '.agento-backup.*')" ]] || fail "a temporary file was left behind"

step "restore refuses a live installation and a non-empty database (nothing changes)"
if "$OPS/restore-into-empty.sh" --project "$PROJECT" --env-file "$ENV_FILE" "$dump" 2>"$WORK/refusal"; then
  fail "restore ran against a live installation"
fi
grep -q "is running against this database" "$WORK/refusal" || fail "unexpected refusal: $(cat "$WORK/refusal")"
compose stop web api >/dev/null
if "$OPS/restore-into-empty.sh" --project "$PROJECT" --env-file "$ENV_FILE" "$dump" 2>"$WORK/refusal"; then
  fail "restore ran against a non-empty database"
fi
grep -q "not empty" "$WORK/refusal" || fail "unexpected refusal: $(cat "$WORK/refusal")"
[[ "$(psql_value "SELECT count(*) FROM product.agent_configurations")" == "$rows" ]] \
  || fail "a refused restore changed the database"

step "destroy the installation's database volume; start a fresh, EMPTY database"
compose down -v >/dev/null
compose up -d --wait --wait-timeout 180 postgres
[[ "$(user_schemas)" == 0 ]] || fail "fresh database is not empty"

step "a backup that does not match its checksum is refused before any change"
cp "$dump" "$BACKUPS/tampered.dump"
printf 'x' >> "$BACKUPS/tampered.dump"
cp "$dump.sha256" "$BACKUPS/tampered.dump.sha256"
if "$OPS/restore-into-empty.sh" --project "$PROJECT" --env-file "$ENV_FILE" "$BACKUPS/tampered.dump" 2>"$WORK/refusal"; then
  fail "a tampered backup was restored"
fi
grep -q "does not match its checksum" "$WORK/refusal" || fail "unexpected refusal: $(cat "$WORK/refusal")"
[[ "$(user_schemas)" == 0 ]] || fail "a refused restore changed the database"

step "restore into the empty database, then the explicit Product migration"
"$OPS/restore-into-empty.sh" --project "$PROJECT" --env-file "$ENV_FILE" "$dump"
[[ "$(psql_value "SELECT version_num FROM product.alembic_version")" == 0009 ]] \
  || fail "Product schema is not at 0009 after the restore"
[[ "$(compose ps --status running --services | tr '\n' ' ')" == "postgres " ]] \
  || fail "the restore started a Product service"

step "start the restored Product explicitly: ready, and the known state survived"
compose up -d --wait --wait-timeout 300 web
[[ "$(status_of "$WEB/api/product/health/ready")" == 200 ]] || fail "restored installation not ready"
curl -sf -H "$(key)" "$WEB/api/product/system/status" | python3 -c '
import json, sys
body = json.load(sys.stdin)
assert body["overall"] == "ready" and body["reasons"] == [], body
assert body["components"]["product_schema"] == "ready", body
print("System Status after restore: ready")'
[[ "$(agent_enabled)" == False ]] || fail "the disabled operations Agent did not survive the restore"
echo "known Product state survived: operations Agent disabled"

step "backup / restore drill passed"
