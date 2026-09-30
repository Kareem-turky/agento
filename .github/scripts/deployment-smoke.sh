#!/usr/bin/env bash
# CI-only smoke test of the Product deployment package (deployments/template).
#
#   .github/scripts/deployment-smoke.sh <api-image>
#
# Fresh volume -> PostgreSQL healthy -> migration job exits 0 -> API healthy, then
# checks the packaged runtime: non-root, read-only root, /tmp, AgentOS auth, disabled
# business routes, no dev tools, no runtime secret in the image, clean shutdown and
# production fail-closed. Every credential is a throwaway CI-only value generated here;
# none is printed. No model, provider or external integration call is made.
set -euo pipefail

IMAGE="${1:?usage: deployment-smoke.sh <api-image>}"
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
TEMPLATE="$ROOT/deployments/template"
PROJECT="cap-deploy-smoke"
PORT="18080"
API="http://127.0.0.1:$PORT"
WORK="$(mktemp -d)"
ENV_FILE="$WORK/smoke.env"

# Unmistakable, per-run CI-only markers (runtime inputs only; never baked into the image).
RUN_ID="$(od -An -N8 -tx1 /dev/urandom | tr -d ' \n')"
PG_PASSWORD="ciOnlyPgMarker${RUN_ID}"
OS_KEY="ci-only-agentos-marker-${RUN_ID}-000000000000"
PRODUCT_KEY="ci-only-product-key-marker-${RUN_ID}-0000000000"
PRODUCT_KEY_SHA="$(printf '%s' "$PRODUCT_KEY" | sha256sum | cut -d' ' -f1)"
STORE="0b0b0b0b-0000-4000-8000-000000000001"
PRINCIPALS="[{\"key_id\":\"ci-smoke\",\"key_sha256\":\"$PRODUCT_KEY_SHA\",\"actor_id\":\"ci-smoke-client\",\"role_ids\":[\"operations\"],\"permissions\":[\"orders.read\",\"tickets.create\"],\"store_ids\":[\"$STORE\"]}]"

compose() {
  docker compose -p "$PROJECT" -f "$TEMPLATE/compose.yaml" --env-file "$ENV_FILE" "$@"
}

cleanup() {
  status=$?
  if [[ "$status" != 0 ]]; then
    compose ps -a || true
    compose logs --no-color || true
  fi
  compose down -v --remove-orphans >/dev/null 2>&1 || true
  docker rm -f "$PROJECT-probe" >/dev/null 2>&1 || true
  rm -rf "$WORK"
}
trap cleanup EXIT

step() { printf '\n== %s\n' "$*"; }
fail() { printf 'FAIL: %s\n' "$*" >&2; exit 1; }
status_of() { curl -s -o /dev/null -w '%{http_code}' "$@"; }
container_of() { compose ps -a -q "$1"; }

umask 077
cat > "$ENV_FILE" <<EOF
API_IMAGE=$IMAGE
PRODUCT_API_PORT=$PORT
POSTGRES_DB=platform
POSTGRES_USER=platform
POSTGRES_PASSWORD=$PG_PASSWORD
APP_ENVIRONMENT=test
APP_LOG_LEVEL=INFO
APP_PRODUCT_AUTH_MODE=api_key
APP_COMPANY_ID=ci-smoke-company
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

step "deployment Compose configuration is valid"
compose config --quiet

step "image: explicit non-root user, exec-form command, no migration in the command"
user="$(docker image inspect "$IMAGE" --format '{{.Config.User}}')"
[[ "$user" =~ ^[0-9]+:[0-9]+$ && "${user%%:*}" != 0 ]] || fail "image user is '$user'"
cmd="$(docker image inspect "$IMAGE" --format '{{json .Config.Cmd}}')"
[[ "$cmd" == '["python","-m","uvicorn","app.bootstrap:create_deployment_app",'* ]] \
  || fail "unexpected image command"
[[ "$cmd" != *alembic* && "$cmd" != *--reload* ]] || fail "image command migrates or reloads"
[[ "$(docker image inspect "$IMAGE" --format '{{json .Config.Entrypoint}}')" == null ]] \
  || fail "image has an entrypoint"
echo "user=$user"

step "image: runtime content and no development tools"
docker run --rm -i --network none "$IMAGE" python - <<'PY'
import importlib.util, os, sys
sys.path.insert(0, "/app/apps/api")
for name in ("app.bootstrap", "fastapi", "agno", "sqlalchemy", "alembic",
             "opentelemetry.trace", "httpx", "uvicorn", "psycopg"):
    assert importlib.util.find_spec(name) is not None, name
# Development-only tools (dependency group "dev") must not be installed.
for name in ("pytest", "ruff", "_pytest"):
    assert importlib.util.find_spec(name) is None, name
for path in ("/app/tests", "/app/.git", "/app/.env", "/app/.env.example", "/app/apps/web",
             "/app/deployments", "/app/infra", "/app/pyproject.toml", "/app/.venv",
             "/app/.pytest_cache", "/app/.ruff_cache"):
    assert not os.path.exists(path), path
assert os.path.isfile("/app/alembic.ini")
assert sorted(os.listdir("/app/apps/api/migrations/versions")) >= [
    "0001_create_write_commands.py", "0002_create_audit_events.py"]
for top in ("/app", "/opt/venv"):
    for base, dirs, files in os.walk(top):
        for name in dirs + files:
            assert name not in (".git", "node_modules", ".next", ".pytest_cache", ".env"), (
                os.path.join(base, name))
print("runtime content ok")
PY

step "fresh PostgreSQL volume becomes healthy (no host port)"
compose up -d --wait --wait-timeout 180 postgres
[[ -z "$(docker port "$(container_of postgres)")" ]] || fail "postgres publishes a host port"

step "a failed migration keeps the API down"
cat > "$WORK/failing-migration.yaml" <<'EOF'
services:
  migrate:
    command: ["python", "-c", "raise SystemExit(3)"]
EOF
if compose -f "$WORK/failing-migration.yaml" up -d api >/dev/null 2>&1; then
  fail "api started although the migration failed"
fi
api_id="$(container_of api)"
if [[ -n "$api_id" ]]; then
  [[ "$(docker inspect "$api_id" --format '{{.State.Running}} {{.State.StartedAt}}')" \
     == "false 0001-01-01T00:00:00Z" ]] || fail "api container ran after a failed migration"
fi
compose rm -f -s -v migrate api >/dev/null
echo "api not started after migration exit 3"

step "migration job (same image) runs and exits 0"
compose up --no-log-prefix migrate
migrate_exit="$(docker inspect "$(container_of migrate)" --format '{{.State.ExitCode}}')"
[[ "$migrate_exit" == 0 ]] || fail "migration exited $migrate_exit"
tables="$(compose exec -T postgres psql -U platform -d platform -v ON_ERROR_STOP=1 -tAc \
  "SELECT string_agg(table_name, ',' ORDER BY table_name) FROM information_schema.tables
   WHERE table_schema = 'product'")"
echo "product tables: $tables"
[[ ",$tables," == *",write_commands,"* && ",$tables," == *",audit_events,"* ]] \
  || fail "Product tables missing"
compose exec -T postgres psql -U platform -d platform -tAc \
  "SELECT extversion FROM pg_extension WHERE extname = 'vector'" | grep -Eq '^[0-9]' \
  || fail "pgvector extension missing"

step "API starts only now and becomes healthy"
compose up -d --wait --wait-timeout 180 api
api_id="$(container_of api)"
health="$(curl -sf "$API/health")"
python3 - "$health" <<'PY'
import json, sys
body = json.loads(sys.argv[1])
assert body["status"] == "ok", body
assert body["agent_runtime"]["status"] == "ready", body
assert body["application"]["environment"] == "test", body
print("health ok, agent runtime ready")
PY

step "API is published on 127.0.0.1 only"
bindings="$(docker port "$api_id")"
echo "$bindings"
[[ -n "$bindings" ]] || fail "api is not published"
while read -r line; do
  [[ "$line" == "8000/tcp -> 127.0.0.1:$PORT" ]] || fail "unexpected binding: $line"
done <<< "$bindings"

step "AgentOS authentication"
[[ "$(status_of "$API/agents")" == 401 ]] || fail "/agents without key is not 401"
[[ "$(status_of -H "Authorization: Bearer $OS_KEY" "$API/agents")" == 200 ]] \
  || fail "/agents with the OS key is not 200"

step "Product business routes stay unconfigured (disabled backend)"
run='{"message":"hi","store_id":"'"$STORE"'"}'
[[ "$(status_of -X POST -H 'Content-Type: application/json' -d "$run" \
      "$API/api/v1/operations/runs")" == 401 ]] || fail "runs without a key is not 401"
[[ "$(status_of -X POST -H 'Content-Type: application/json' \
      -H "Authorization: Bearer $PRODUCT_KEY" -d "$run" \
      "$API/api/v1/operations/runs")" == 503 ]] || fail "runs is not 503 with disabled backend"

step "runtime user is non-root"
uid="$(docker exec "$api_id" id -u)"
echo "uid=$uid gid=$(docker exec "$api_id" id -g)"
[[ "$uid" != 0 && "$uid" == "${user%%:*}" ]] || fail "API runs as uid $uid"
[[ "$(docker inspect "$api_id" --format '{{.HostConfig.ReadonlyRootfs}}')" == true ]] \
  || fail "root filesystem is not read-only"

step "read-only root filesystem, writable /tmp only"
docker exec -i "$api_id" python - <<'PY'
import errno, os
for path in ("/rootfs-write-probe", "/app/rootfs-write-probe",
             "/app/apps/api/app/rootfs-write-probe", "/opt/venv/rootfs-write-probe"):
    try:
        open(path, "w").close()
    except OSError as error:
        assert error.errno in (errno.EROFS, errno.EACCES), (path, error.errno)
    else:
        raise SystemExit(f"write succeeded: {path}")
probe = "/tmp/tmpfs-write-probe"
with open(probe, "w") as handle:
    handle.write("ok")
os.remove(probe)
assert not os.path.exists(probe)
print("root read-only; /tmp writable and cleaned")
PY

step "no runtime secret in image history, configuration or filesystem"
for marker in "$PG_PASSWORD" "$OS_KEY" "$PRODUCT_KEY" "$RUN_ID"; do
  if docker history --no-trunc "$IMAGE" | grep -qF "$marker"; then fail "marker in history"; fi
  if docker image inspect "$IMAGE" | grep -qF "$marker"; then fail "marker in image config"; fi
done
docker create --name "$PROJECT-probe" "$IMAGE" >/dev/null
if docker export "$PROJECT-probe" | grep -aqF "$RUN_ID"; then fail "marker in image filesystem"; fi
docker rm "$PROJECT-probe" >/dev/null
docker image inspect "$IMAGE" --format '{{json .Config.Env}}' \
  | grep -Eqi 'PASSWORD|SECURITY_KEY|API_KEY|DATABASE_URL|TOKEN' && fail "secret-like ENV in image"
echo "no runtime marker found in the image"

step "clean shutdown on SIGTERM (docker stop)"
start="$(date +%s)"
docker stop -t 30 "$api_id" >/dev/null
elapsed="$(( $(date +%s) - start ))"
exit_code="$(docker inspect "$api_id" --format '{{.State.ExitCode}}')"
echo "stopped in ${elapsed}s with exit code $exit_code"
[[ "$exit_code" == 0 && "$elapsed" -lt 30 ]] || fail "unclean shutdown"
docker logs "$api_id" 2>&1 | grep -q "Finished server process" || fail "no graceful shutdown"

step "production without a real business backend still fails closed"
for backend in disabled mock; do
  set +e
  output="$(docker run --rm --read-only --tmpfs /tmp --network "${PROJECT}_database" \
    -e APP_ENVIRONMENT=production -e APP_PRODUCT_AUTH_MODE=api_key \
    -e APP_COMPANY_ID=ci-smoke-company -e APP_PRODUCT_API_KEYS="$PRINCIPALS" \
    -e APP_BUSINESS_BACKEND="$backend" -e APP_DEFAULT_MODEL_PROVIDER=disabled \
    -e OS_SECURITY_KEY="$OS_KEY" -e AGNO_TELEMETRY=false \
    -e APP_DATABASE_URL="postgresql+psycopg://platform:$PG_PASSWORD@postgres:5432/platform" \
    "$IMAGE" 2>&1)"
  code=$?
  set -e
  [[ "$code" != 0 ]] || fail "production with backend '$backend' started"
  expected="no business backend is available for staging/production deployments"
  [[ "$backend" == mock ]] && expected="selected business backend is not allowed in this environment"
  grep -qF "$expected" <<< "$output" || fail "production '$backend' failed for another reason"
  grep -qF "$RUN_ID" <<< "$output" && fail "startup failure output contains a secret"
  echo "production + $backend: refused (exit $code)"
done

step "deployment smoke passed"
