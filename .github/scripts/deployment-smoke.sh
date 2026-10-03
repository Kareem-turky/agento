#!/usr/bin/env bash
# CI-only smoke test of the Product deployment package (deployments/template).
#
#   .github/scripts/deployment-smoke.sh <api-image> <web-image>
#
# Fresh volume -> PostgreSQL healthy -> migration job exits 0 -> API healthy (private)
# -> Web healthy (the only host-published service), then checks the Product from the
# USER-FACING Web port: Operations Console, BFF -> private API, Product auth, disabled
# business routes, Web/API/AgentOS isolation, non-root and read-only runtimes, secret
# distribution and non-leak, clean shutdown and production fail-closed. Every credential
# is a throwaway CI-only value generated here; none is printed. No model, provider or
# external integration call is made.
set -euo pipefail

API_IMAGE="${1:?usage: deployment-smoke.sh <api-image> <web-image>}"
WEB_IMAGE="${2:?usage: deployment-smoke.sh <api-image> <web-image>}"
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
TEMPLATE="$ROOT/deployments/template"
PROJECT="cap-deploy-smoke"
WEB_PORT="13080"
WEB="http://127.0.0.1:$WEB_PORT"
WORK="$(mktemp -d)"
ENV_FILE="$WORK/smoke.env"

# Unmistakable, per-run CI-only markers (runtime inputs only; never baked into an image).
RUN_ID="$(od -An -N8 -tx1 /dev/urandom | tr -d ' \n')"
PG_PASSWORD="ciOnlyPgMarker${RUN_ID}"
OS_KEY="ci-only-agentos-marker-${RUN_ID}-000000000000"
PRODUCT_KEY="ci-only-product-key-marker-${RUN_ID}-0000000000"
PRODUCT_KEY_SHA="$(printf '%s' "$PRODUCT_KEY" | sha256sum | cut -d' ' -f1)"
OPS_MESSAGE="ci-only-operations-message-${RUN_ID}"
STORE="0b0b0b0b-0000-4000-8000-000000000001"
PRINCIPALS="[{\"key_id\":\"ci-smoke\",\"key_sha256\":\"$PRODUCT_KEY_SHA\",\"actor_id\":\"ci-smoke-client\",\"role_ids\":[\"operations\"],\"permissions\":[\"orders.read\",\"tickets.create\",\"system.read\"],\"store_ids\":[\"$STORE\"]}]"
PRIVATE_ORIGIN="http://api:8000"

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
never_started() {
  local id="$1"
  [[ -z "$id" ]] && return 0
  [[ "$(docker inspect "$id" --format '{{.State.Running}} {{.State.StartedAt}}')" \
     == "false 0001-01-01T00:00:00Z" ]]
}
# A request from INSIDE the API container (the API has no host port).
api_internal() {
  docker exec -i "$1" python - "$2" "$3" <<'PY'
import os, sys, urllib.error, urllib.request
path, auth = sys.argv[1], sys.argv[2]
request = urllib.request.Request(f"http://127.0.0.1:8000{path}")
if auth == "os-key":  # read inside the container: the key never crosses the command line
    request.add_header("Authorization", f"Bearer {os.environ['OS_SECURITY_KEY']}")
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
try:
    with opener.open(request, timeout=10) as response:
        print(response.status, response.read().decode())
except urllib.error.HTTPError as error:
    print(error.code)
PY
}

write_env() {
  cat <<EOF
API_IMAGE=$API_IMAGE
WEB_IMAGE=$WEB_IMAGE
PRODUCT_WEB_PORT=$WEB_PORT
POSTGRES_DB=platform
POSTGRES_USER=platform
POSTGRES_PASSWORD=$PG_PASSWORD
APP_ENVIRONMENT=$1
APP_LOG_LEVEL=INFO
APP_OTEL_EXPORT_MODE=disabled
APP_OTEL_EXPORT_ENDPOINT=
APP_PRODUCT_AUTH_MODE=api_key
APP_COMPANY_ID=ci-smoke-company
APP_PRODUCT_API_KEYS=$PRINCIPALS
OS_SECURITY_KEY=$OS_KEY
APP_BUSINESS_BACKEND=$2
APP_BACKEND_CONFIG_DIR=
APP_BACKEND_SECRETS_DIR=
APP_DEFAULT_MODEL_PROVIDER=disabled
APP_DEFAULT_MODEL_ID=
OPENAI_API_KEY=
ANTHROPIC_API_KEY=
EOF
}

umask 077
write_env test disabled > "$ENV_FILE"

step "deployment Compose configuration is valid: exactly postgres, migrate, api, web"
compose config --quiet
[[ "$(compose config --services | sort | tr '\n' ' ')" == "api migrate postgres web " ]] \
  || fail "unexpected services: $(compose config --services | tr '\n' ' ')"

step "API image: explicit non-root user, exec-form command, no migration in the command"
api_user="$(docker image inspect "$API_IMAGE" --format '{{.Config.User}}')"
[[ "$api_user" == "10001:10001" ]] || fail "API image user is '$api_user'"
cmd="$(docker image inspect "$API_IMAGE" --format '{{json .Config.Cmd}}')"
[[ "$cmd" == '["python","-m","uvicorn","app.bootstrap:create_deployment_app",'* ]] \
  || fail "unexpected API image command"
[[ "$cmd" != *alembic* && "$cmd" != *--reload* ]] || fail "API command migrates or reloads"
[[ "$(docker image inspect "$API_IMAGE" --format '{{json .Config.Entrypoint}}')" == null ]] \
  || fail "API image has an entrypoint"

step "API image: runtime content and no development tools"
docker run --rm -i --network none "$API_IMAGE" python - <<'PY'
import importlib.util, os, sys
sys.path.insert(0, "/app/apps/api")
for name in ("app.bootstrap", "fastapi", "agno", "sqlalchemy", "alembic",
             "opentelemetry.trace", "httpx", "uvicorn", "psycopg",
             # Task 039: optional OTLP/HTTP export (used only when explicitly enabled).
             "opentelemetry.sdk.trace", "opentelemetry.exporter.otlp.proto.http"):
    assert importlib.util.find_spec(name) is not None, name
for name in ("pytest", "ruff", "_pytest"):
    assert importlib.util.find_spec(name) is None, name
for path in ("/app/tests", "/app/.git", "/app/.env", "/app/.env.example", "/app/apps/web",
             "/app/deployments", "/app/infra", "/app/pyproject.toml", "/app/.venv"):
    assert not os.path.exists(path), path
assert os.path.isfile("/app/alembic.ini")
print("API runtime content ok")
PY

step "Web image: explicit non-root user, node server.js directly, no baked origin"
web_user="$(docker image inspect "$WEB_IMAGE" --format '{{.Config.User}}')"
[[ "$web_user" == "10002:10002" ]] || fail "Web image user is '$web_user'"
[[ "$(docker image inspect "$WEB_IMAGE" --format '{{json .Config.Cmd}}')" == '["node","server.js"]' ]] \
  || fail "unexpected Web image command"
web_entry="$(docker image inspect "$WEB_IMAGE" --format '{{json .Config.Entrypoint}}')"
[[ "$web_entry" == null || "$web_entry" == "[]" ]] || fail "Web image has an entrypoint: $web_entry"
web_env="$(docker image inspect "$WEB_IMAGE" --format '{{json .Config.Env}}')"
[[ "$web_env" == *'"NEXT_TELEMETRY_DISABLED=1"'* && "$web_env" == *'"NODE_ENV=production"'* ]] \
  || fail "Web image telemetry/production environment missing"
[[ "$web_env" != *PRODUCT_API_ORIGIN* ]] || fail "PRODUCT_API_ORIGIN baked into the Web image"
echo "web user=$web_user"

step "Web image: standalone runtime content only, no dev tooling"
docker run --rm -i --network none --entrypoint node "$WEB_IMAGE" - <<'JS'
const fs = require("fs"), path = require("path");
const top = fs.readdirSync("/app").sort().join(",");
if (top !== ".next,node_modules,package.json,server.js") throw new Error(`unexpected /app: ${top}`);
for (const p of ["/app/node_modules/typescript", "/app/node_modules/@types", "/app/scripts",
                 "/app/tests", "/app/.git", "/app/.env", "/app/.env.local", "/app/.env.example",
                 "/app/app", "/app/components", "/app/lib", "/app/apps", "/app/deployments",
                 "/app/Dockerfile", "/app/tsconfig.json"]) {
  if (fs.existsSync(p)) throw new Error(`present: ${p}`);
}
const bad = [".git", ".env", ".env.local", ".env.example", "product-proxy-smoke.mjs",
             "session-epoch.test.mjs", "deployment-smoke.sh", "compose.yaml", "alembic.ini"];
(function walk(dir) {
  for (const name of fs.readdirSync(dir)) {
    const full = path.join(dir, name);
    if (bad.includes(name)) throw new Error(`present: ${full}`);
    if (fs.lstatSync(full).isDirectory()) walk(full);
  }
})("/app");
console.log("Web runtime content ok");
JS

step "fresh PostgreSQL volume becomes healthy (no host port)"
compose up -d --wait --wait-timeout 180 postgres
[[ -z "$(docker port "$(container_of postgres)")" ]] || fail "postgres publishes a host port"

step "a failed migration keeps both the API and the Web down"
cat > "$WORK/failing-migration.yaml" <<'EOF'
services:
  migrate:
    command: ["python", "-c", "raise SystemExit(3)"]
EOF
if compose -f "$WORK/failing-migration.yaml" up -d web >/dev/null 2>&1; then
  fail "the stack started although the migration failed"
fi
never_started "$(container_of api)" || fail "api container ran after a failed migration"
never_started "$(container_of web)" || fail "web container ran after a failed migration"
compose rm -f -s -v migrate api web >/dev/null
echo "api and web not started after migration exit 3"

step "migration job (same API image) runs and exits 0"
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

step "API becomes healthy (privately), then Web becomes healthy"
compose up -d --wait --wait-timeout 240 web
api_id="$(container_of api)"
web_id="$(container_of web)"
[[ "$(docker inspect "$api_id" --format '{{.State.Health.Status}}')" == healthy ]] || fail "api not healthy"
[[ "$(docker inspect "$web_id" --format '{{.State.Health.Status}}')" == healthy ]] || fail "web not healthy"
api_internal "$api_id" /health none | python3 -c '
import json, sys
status, body = sys.stdin.read().split(" ", 1)
body = json.loads(body)
assert status == "200" and body["status"] == "ok", body
assert body["agent_runtime"]["status"] == "ready", body
assert body["application"]["environment"] == "test", body
print("API healthy internally, agent runtime ready")'

step "Task 039: liveness and readiness are public and minimal; System Status is authenticated"
for path in /health/live /health/ready; do
  answer="$(api_internal "$api_id" "$path" none)"
  expected='200 {"status":"alive"}'
  [[ "$path" == /health/ready ]] && expected='200 {"status":"ready"}'
  [[ "$answer" == "$expected" ]] || fail "$path answered: $answer"
done
[[ "$(curl -sf "$WEB/api/product/health/ready")" == '{"status":"ready"}' ]] || fail "BFF readiness not ready"
[[ "$(status_of "$WEB/api/product/system/status")" == 401 ]] || fail "System Status without a key is not 401"
[[ "$(status_of -H "Authorization: Bearer $OS_KEY" "$WEB/api/product/system/status")" == 401 ]] \
  || fail "System Status accepted the AgentOS key"
system_status="$(curl -sf -H "Authorization: Bearer $PRODUCT_KEY" "$WEB/api/product/system/status")"
python3 -c '
import json, sys
body = json.loads(sys.argv[1])
assert body["overall"] == "ready" and body["reasons"] == [], body
assert body["components"] == {"application": "ready", "database": "ready",
                              "product_schema": "ready", "agent_runtime": "ready"}, body
assert body["observability"] == {"export_mode": "disabled"}, body
assert body["application"]["environment"] == "test", body
print("System Status: ready, every component ready, export disabled")' "$system_status"
for secret in "$PG_PASSWORD" "postgres:5432" "$PRIVATE_ORIGIN" "ci-smoke-company" "ci-smoke-client" \
              "$PRODUCT_KEY_SHA" "$OS_KEY" "/app"; do
  grep -qF "$secret" <<< "$system_status" && fail "System Status exposes a private value"
done
echo "public health minimal; System Status authenticated and value-free"

step "Task 039: Product completion logs are JSON on stdout, at APP_LOG_LEVEL, without secrets"
api_log="$(docker logs "$api_id" 2>&1)"
grep -F '"event":"product.operation.completed"' <<< "$api_log" | head -n 1 | python3 -c '
import json, sys
record = json.loads(sys.stdin.read())
assert record["event"] == "product.operation.completed" and "operation" in record, record
print("structured completion log:", record["operation"], record["outcome"])'
for secret in "$PG_PASSWORD" "$OS_KEY" "$PRODUCT_KEY" "$RUN_ID"; do
  grep -qF "$secret" <<< "$api_log" && fail "API logs contain a credential"
done

step "Task 039: a database outage keeps the API alive, makes it (and Web) not ready, then recovers without a restart"
pg_id="$(container_of postgres)"
api_started="$(docker inspect "$api_id" --format '{{.State.StartedAt}} {{.RestartCount}}')"
docker stop -t 10 "$pg_id" >/dev/null
for _ in $(seq 1 30); do
  [[ "$(api_internal "$api_id" /health/ready none)" == 503 ]] && break
  sleep 1
done
[[ "$(api_internal "$api_id" /health/ready none)" == 503 ]] || fail "readiness did not fail during the outage"
[[ "$(api_internal "$api_id" /health/live none)" == '200 {"status":"alive"}' ]] \
  || fail "liveness failed during the outage"
[[ "$(curl -s "$WEB/api/product/health/ready")" == '{"status":"not_ready"}' ]] || fail "BFF readiness not 503"
[[ "$(status_of "$WEB/api/product/health/ready")" == 503 ]] || fail "BFF readiness status not 503"
# The Web container's own health check command reports the installation unhealthy.
web_check="$(docker inspect "$web_id" --format '{{json .Config.Healthcheck.Test}}' \
             | python3 -c 'import json, sys; print(json.load(sys.stdin)[-1])')"
if docker exec "$web_id" node -e "$web_check"; then fail "Web health check passed during the outage"; fi
outage_status="$(curl -s -H "Authorization: Bearer $PRODUCT_KEY" "$WEB/api/product/system/status")"
grep -q '"database_unavailable"' <<< "$outage_status" || fail "System Status does not explain the outage"
grep -qF "$PG_PASSWORD" <<< "$outage_status" && fail "System Status exposes a secret during the outage"
echo "outage: live 200, ready 503, BFF ready 503, Web health check failing, reason database_unavailable"
docker start "$pg_id" >/dev/null
for _ in $(seq 1 60); do
  [[ "$(api_internal "$api_id" /health/ready none)" == '200 {"status":"ready"}' ]] && break
  sleep 1
done
[[ "$(api_internal "$api_id" /health/ready none)" == '200 {"status":"ready"}' ]] \
  || fail "readiness did not recover after PostgreSQL returned"
[[ "$(status_of "$WEB/api/product/health/ready")" == 200 ]] || fail "BFF readiness did not recover"
docker exec "$web_id" node -e "$web_check" || fail "Web health check did not recover"
[[ "$(docker inspect "$api_id" --format '{{.State.StartedAt}} {{.RestartCount}}')" == "$api_started" ]] \
  || fail "the API was restarted"
echo "recovered: ready again in the SAME API process (no restart)"

step "only Web is host-published, on 127.0.0.1; the API has no host port"
[[ -z "$(docker port "$api_id")" ]] || fail "api publishes a host port: $(docker port "$api_id")"
# `compose port` prints ":0" (or nothing) for a port that is not published.
if compose port api 8000 2>/dev/null | grep -Eq '[0-9]:[1-9][0-9]*$'; then
  fail "compose reports an api host port"
fi
bindings="$(docker port "$web_id")"
echo "$bindings"
while read -r line; do
  [[ "$line" == "3000/tcp -> 127.0.0.1:$WEB_PORT" ]] || fail "unexpected Web binding: $line"
done <<< "$bindings"
[[ "$(docker inspect "$web_id" --format '{{range $n, $_ := .NetworkSettings.Networks}}{{$n}} {{end}}')" \
   == "${PROJECT}_product ${PROJECT}_web-publish " ]] || fail "unexpected Web networks"
[[ "$(docker inspect "$api_id" --format '{{range $n, $_ := .NetworkSettings.Networks}}{{$n}} {{end}}')" \
   == "${PROJECT}_database ${PROJECT}_egress ${PROJECT}_product " ]] || fail "unexpected API networks"
[[ "$(docker network inspect "${PROJECT}_product" --format '{{.Internal}}')" == true ]] \
  || fail "product network is not internal"
[[ "$(docker network inspect "${PROJECT}_web-publish" \
      --format '{{index .Options "com.docker.network.bridge.enable_ip_masquerade"}}')" == false ]] \
  || fail "web-publish network grants outbound NAT"

step "Agento Overview and Operations Console through the Web port; private origin and secrets absent from browser assets"
html="$(curl -sf "$WEB/")"
grep -q "Agento" <<< "$html" || fail "Agento Overview not served"
operations="$(curl -sf "$WEB/operations")"
grep -q "Analyze operations" <<< "$operations" || fail "Operations Console not served at /operations"
html+="$operations"
assets="$(grep -oE '/_next/static/[^"]+\.js' <<< "$html" | sort -u)"
[[ -n "$assets" ]] || fail "no client assets referenced"
browser="$html"
while read -r asset; do
  browser+="$(curl -sf "$WEB$asset")" || fail "asset not served: $asset"
done <<< "$assets"
for secret in "$PRIVATE_ORIGIN" "api:8000" "PRODUCT_API_ORIGIN" "$PRODUCT_KEY" "$OS_KEY" "$PG_PASSWORD" "$RUN_ID"; do
  grep -qF "$secret" <<< "$browser" && fail "browser HTML/JS exposes a private value"
done
echo "console served; $(wc -l <<< "$assets") client assets checked"

step "BFF health and Product authentication through the Web port"
bff_health="$(curl -sf "$WEB/api/product/health")"
[[ "$bff_health" == *'"status":"ok"'* ]] || fail "BFF health is not ok"
run='{"message":"'"$OPS_MESSAGE"'","store_id":"'"$STORE"'"}'
[[ "$(status_of -X POST -H 'Content-Type: application/json' -d "$run" \
      "$WEB/api/product/operations/runs")" == 401 ]] || fail "BFF runs without a key is not 401"
[[ "$(status_of -X POST -H 'Content-Type: application/json' \
      -H "Authorization: Bearer $PRODUCT_KEY" -d "$run" \
      "$WEB/api/product/operations/runs")" == 503 ]] || fail "BFF runs is not 503 (disabled backend)"
[[ "$(status_of -H "Authorization: Bearer $PRODUCT_KEY" \
      "$WEB/api/product/operations/reports/daily?store_id=$STORE")" == 503 ]] \
  || fail "BFF daily report is not 503 (disabled backend)"

step "AgentOS and the raw Product API are not reachable through the Web port"
for path in /agents /info /config /sessions /metrics /docs /openapi.json /api/product/agents \
            /api/product/info /api/product/..%2F..%2Fagents /api/v1/operations/reports/daily; do
  code="$(status_of -H "Authorization: Bearer $OS_KEY" "$WEB$path")"
  [[ "$code" == 404 ]] || fail "$path through Web answered $code"
done
code="$(status_of -X POST -H 'Content-Type: application/json' \
          -H "Authorization: Bearer $PRODUCT_KEY" -d "$run" "$WEB/api/v1/operations/runs")"
[[ "$code" == 404 || "$code" == 405 ]] || fail "/api/v1/operations/runs through Web answered $code"
api_log="$(docker logs "$api_id" 2>&1)"
# The API access log is the witness: the legitimate BFF call must be in it ...
grep -q '"POST /api/v1/operations/runs' <<< "$api_log" || fail "API access log unavailable"
# ... and nothing that only exists on AgentOS or as a non-BFF path.
for path in /agents /info /config /sessions /docs /openapi.json /api/product; do
  grep -q "\"[A-Z]* $path" <<< "$api_log" && fail "$path reached the API through Web"
done
echo "no AgentOS or raw Product API path reached the API through Web"

step "AgentOS authentication, checked inside the private API container"
[[ "$(api_internal "$api_id" /agents none)" == 401 ]] || fail "internal /agents without key is not 401"
[[ "$(api_internal "$api_id" /agents os-key | cut -d' ' -f1)" == 200 ]] \
  || fail "internal /agents with the OS key is not 200"

step "runtime identities are non-root"
[[ "$(docker exec "$api_id" id -u):$(docker exec "$api_id" id -g)" == 10001:10001 ]] || fail "API uid"
[[ "$(docker exec "$web_id" id -u):$(docker exec "$web_id" id -g)" == 10002:10002 ]] || fail "Web uid"
for id in "$api_id" "$web_id"; do
  [[ "$(docker inspect "$id" --format '{{.HostConfig.ReadonlyRootfs}}')" == true ]] \
    || fail "root filesystem is not read-only"
done
echo "api 10001:10001, web 10002:10002, both read-only"

step "read-only root filesystems, writable /tmp only"
docker exec -i "$api_id" python - <<'PY'
import errno, os
for path in ("/rootfs-write-probe", "/app/rootfs-write-probe", "/opt/venv/rootfs-write-probe"):
    try:
        open(path, "w").close()
    except OSError as error:
        assert error.errno in (errno.EROFS, errno.EACCES), (path, error.errno)
    else:
        raise SystemExit(f"write succeeded: {path}")
with open("/tmp/tmpfs-write-probe", "w") as handle:
    handle.write("ok")
os.remove("/tmp/tmpfs-write-probe")
print("API: root read-only; /tmp writable and cleaned")
PY
docker exec -i "$web_id" node - <<'JS'
const fs = require("fs");
for (const p of ["/rootfs-write-probe", "/app/rootfs-write-probe", "/app/.next/rootfs-write-probe",
                 "/app/node_modules/rootfs-write-probe"]) {
  try { fs.writeFileSync(p, "x"); } catch (e) {
    if (!["EROFS", "EACCES"].includes(e.code)) throw e; continue;
  }
  throw new Error(`write succeeded: ${p}`);
}
fs.writeFileSync("/tmp/tmpfs-write-probe", "ok");
fs.rmSync("/tmp/tmpfs-write-probe");
if (fs.existsSync("/tmp/tmpfs-write-probe")) throw new Error("probe not cleaned");
console.log("Web: root read-only; /tmp writable and cleaned");
JS

step "Web receives only its private API origin: no database, AgentOS, model or backend secret"
web_runtime_env="$(docker inspect "$web_id" --format '{{json .Config.Env}}')"
[[ "$web_runtime_env" == *"\"PRODUCT_API_ORIGIN=$PRIVATE_ORIGIN\""* ]] || fail "Web origin not wired"
for name in POSTGRES_PASSWORD POSTGRES_USER APP_DATABASE_URL OS_SECURITY_KEY OPENAI_API_KEY \
            ANTHROPIC_API_KEY APP_BACKEND_CONFIG_DIR APP_BACKEND_SECRETS_DIR APP_PRODUCT_API_KEYS; do
  [[ "$web_runtime_env" != *"\"$name="* ]] || fail "Web receives $name"
done
for secret in "$PG_PASSWORD" "$OS_KEY" "$PRODUCT_KEY_SHA"; do
  [[ "$web_runtime_env" != *"$secret"* ]] || fail "Web environment carries a secret value"
done
echo "Web environment: PRODUCT_API_ORIGIN only (plus image defaults)"

step "no runtime secret in either image; none in the Web logs"
for image in "$API_IMAGE" "$WEB_IMAGE"; do
  for marker in "$PG_PASSWORD" "$OS_KEY" "$PRODUCT_KEY" "$RUN_ID"; do
    if docker history --no-trunc "$image" | grep -qF "$marker"; then fail "marker in $image history"; fi
    if docker image inspect "$image" | grep -qF "$marker"; then fail "marker in $image config"; fi
  done
  docker create --name "$PROJECT-probe" "$image" >/dev/null
  if docker export "$PROJECT-probe" | grep -aqF "$RUN_ID"; then fail "marker in $image filesystem"; fi
  docker rm "$PROJECT-probe" >/dev/null
  docker image inspect "$image" --format '{{json .Config.Env}}' \
    | grep -Eqi 'PASSWORD|SECURITY_KEY|API_KEY|DATABASE_URL|TOKEN' && fail "secret-like ENV in $image"
done
web_log="$(docker logs "$web_id" 2>&1)"
for secret in "$PRODUCT_KEY" "$RUN_ID" "Bearer" "Authorization" "Idempotency-Key" "$OPS_MESSAGE"; do
  grep -qF "$secret" <<< "$web_log" && fail "Web logs contain a credential or request content"
done
echo "no runtime marker in the images; Web logs clean"

step "clean shutdown on SIGTERM (docker stop): Web first, then API"
start="$(date +%s)"
docker stop -t 30 "$web_id" >/dev/null
elapsed="$(( $(date +%s) - start ))"
web_exit="$(docker inspect "$web_id" --format '{{.State.ExitCode}} {{.State.OOMKilled}}')"
echo "web stopped in ${elapsed}s: $web_exit"
# Next's own SIGTERM handler closes the server and exits 143 (128+SIGTERM); 137 would
# mean SIGKILL after the timeout.
[[ ("$web_exit" == "0 false" || "$web_exit" == "143 false") && "$elapsed" -lt 30 ]] \
  || fail "unclean Web shutdown"
start="$(date +%s)"
docker stop -t 30 "$api_id" >/dev/null
elapsed="$(( $(date +%s) - start ))"
api_exit="$(docker inspect "$api_id" --format '{{.State.ExitCode}}')"
echo "api stopped in ${elapsed}s with exit code $api_exit"
[[ "$api_exit" == 0 && "$elapsed" -lt 30 ]] || fail "unclean API shutdown"
docker logs "$api_id" 2>&1 | grep -q "Finished server process" || fail "no graceful API shutdown"

step "production without a real business backend still fails closed (and Web never starts)"
for backend in disabled mock; do
  set +e
  output="$(docker run --rm --read-only --tmpfs /tmp --network "${PROJECT}_database" \
    -e APP_ENVIRONMENT=production -e APP_PRODUCT_AUTH_MODE=api_key \
    -e APP_COMPANY_ID=ci-smoke-company -e APP_PRODUCT_API_KEYS="$PRINCIPALS" \
    -e APP_BUSINESS_BACKEND="$backend" -e APP_DEFAULT_MODEL_PROVIDER=disabled \
    -e OS_SECURITY_KEY="$OS_KEY" -e AGNO_TELEMETRY=false \
    -e APP_DATABASE_URL="postgresql+psycopg://platform:$PG_PASSWORD@postgres:5432/platform" \
    "$API_IMAGE" 2>&1)"
  code=$?
  set -e
  [[ "$code" != 0 ]] || fail "production with backend '$backend' started"
  expected="no business backend is available for staging/production deployments"
  [[ "$backend" == mock ]] && expected="selected business backend is not allowed in this environment"
  grep -qF "$expected" <<< "$output" || fail "production '$backend' failed for another reason"
  grep -qF "$RUN_ID" <<< "$output" && fail "startup failure output contains a secret"
  echo "production + $backend: API refused (exit $code)"
done
compose rm -f -s -v web api >/dev/null
write_env production disabled > "$WORK/production.env"
if timeout 180 docker compose -p "$PROJECT" -f "$TEMPLATE/compose.yaml" \
     --env-file "$WORK/production.env" up -d web >/dev/null 2>&1; then
  fail "the production stack started without a real business backend"
fi
never_started "$(container_of web)" || fail "web started in production without a backend"
echo "production package: API never healthy, Web never started"

step "deployment smoke passed"
