#!/usr/bin/env bash
# Runnable LOCAL Product demo (Task 030). Prerequisites: Docker with Docker Compose v2,
# plus standard shell tools (od, sha256sum or shasum). No Python, Node, uv or npm needed.
#
#   ./scripts/demo.sh up           build the images, start the demo, print how to use it
#   ./scripts/demo.sh down         stop the demo (keeps the database: commands survive)
#   ./scripts/demo.sh reset        stop, DELETE the demo database and credentials
#   ./scripts/demo.sh status       stack state, Web URL, health, store and date
#   ./scripts/demo.sh credentials  Product API key, Store UUID, date and Web URL
#
# It runs the REAL Product (same API/Web images, same postgres -> migrate -> api -> web
# services of deployments/template) with the existing deterministic mock commerce
# backend and the LOCAL-DEMO-ONLY deterministic model (deployments/demo). It is not a
# production installation: those stay fail-closed without a real business backend.
#
# Secrets are generated locally, per demo environment, into deployments/demo/.runtime/
# (git-ignored, mode 600): the API receives only the SHA-256 of the Product API key; the
# raw key is shown to you (you paste it into the browser) and never given to a container.
#
# Optional environment (used by CI): DEMO_API_IMAGE, DEMO_WEB_IMAGE, DEMO_SKIP_BUILD=1,
# DEMO_PROJECT, DEMO_WEB_PORT, DEMO_RUNTIME_DIR.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TEMPLATE_COMPOSE="$ROOT/deployments/template/compose.yaml"
DEMO_COMPOSE="$ROOT/deployments/demo/compose.override.yaml"
RUNTIME_DIR="${DEMO_RUNTIME_DIR:-$ROOT/deployments/demo/.runtime}"
RUNTIME_ENV="$RUNTIME_DIR/runtime.env"      # Compose inputs: hashes and backend secrets
CREDENTIALS="$RUNTIME_DIR/credentials"      # what the person needs: raw key, store, date
PROJECT="${DEMO_PROJECT:-commerce-ai-platform-demo}"
API_IMAGE="${DEMO_API_IMAGE:-commerce-ai-platform-api:demo}"
WEB_IMAGE="${DEMO_WEB_IMAGE:-commerce-ai-platform-web:demo}"
WEB_PORT="${DEMO_WEB_PORT:-3000}"
WEB_URL="http://127.0.0.1:$WEB_PORT"
DEMO_DATE="2026-03-03"   # the canonical deterministic dataset date (not "today")
SUGGESTED="Analyze operations for $DEMO_DATE."

die() { printf 'demo: %s\n' "$*" >&2; exit 1; }
say() { printf 'demo: %s\n' "$*" >&2; }

compose() {
  docker compose -p "$PROJECT" -f "$TEMPLATE_COMPOSE" -f "$DEMO_COMPOSE" \
    --env-file "$RUNTIME_ENV" "$@"
}

require_docker() {
  command -v docker >/dev/null 2>&1 || die "Docker is required (https://docs.docker.com/get-docker/)"
  docker compose version >/dev/null 2>&1 || die "Docker Compose v2 is required ('docker compose')"
  docker info >/dev/null 2>&1 || die "the Docker daemon is not running or not reachable"
}

random_hex() {  # $1 = number of random bytes (from the kernel CSPRNG)
  od -An -N"$1" -tx1 /dev/urandom | tr -d ' \n'
}

sha256_hex() {  # reads stdin
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum | cut -d' ' -f1
  elif command -v shasum >/dev/null 2>&1; then
    shasum -a 256 | cut -d' ' -f1
  else
    die "sha256sum or shasum is required"
  fi
}

value_of() {  # $1 = file, $2 = NAME
  sed -n "s/^$2=//p" "$1" | head -n 1
}

build_images() {
  if [[ "${DEMO_SKIP_BUILD:-0}" == 1 ]]; then
    docker image inspect "$API_IMAGE" >/dev/null 2>&1 || die "image $API_IMAGE not found"
    docker image inspect "$WEB_IMAGE" >/dev/null 2>&1 || die "image $WEB_IMAGE not found"
    return
  fi
  say "building the Product API image ($API_IMAGE)"
  docker build -f "$ROOT/apps/api/Dockerfile" -t "$API_IMAGE" "$ROOT"
  say "building the Product Web image ($WEB_IMAGE)"
  docker build -f "$ROOT/apps/web/Dockerfile" -t "$WEB_IMAGE" "$ROOT"
}

# The canonical company and South store UUIDs, derived by the Product's own mock fixture
# algorithm inside the API image (offline, read-only, no network): never hand-copied.
canonical_ids() {
  docker run --rm --network none --read-only "$API_IMAGE" python -c '
import sys
sys.path.insert(0, "/app/apps/api")
from app.integrations.commerce.mock import EntityType, canonical_id
print(canonical_id(EntityType.COMPANY, "acct_demo"))
print(canonical_id(EntityType.STORE, "shop_south"))
'
}

generate_runtime() {
  local ids company store product_key os_key pg_password key_sha principals
  ids="$(canonical_ids)" || die "could not derive the canonical demo identities"
  company="$(sed -n 1p <<< "$ids")"
  store="$(sed -n 2p <<< "$ids")"
  [[ "$company" =~ ^[0-9a-f-]{36}$ && "$store" =~ ^[0-9a-f-]{36}$ ]] \
    || die "unexpected canonical identities"
  product_key="demo-product-key-$(random_hex 24)"
  os_key="$(random_hex 32)"
  pg_password="$(random_hex 24)"
  [[ "$product_key" != "$os_key" ]] || die "key generation failed"
  key_sha="$(printf '%s' "$product_key" | sha256_hex)"
  [[ "$key_sha" =~ ^[0-9a-f]{64}$ ]] || die "could not hash the Product API key"
  principals="[{\"key_id\":\"demo-operator\",\"key_sha256\":\"$key_sha\",\"actor_id\":\"demo-operator\",\"role_ids\":[\"operations\"],\"permissions\":[\"stores.read\",\"orders.read\",\"shipments.read\",\"tickets.create\"],\"store_ids\":[\"$store\"]}]"
  mkdir -p "$RUNTIME_DIR"
  chmod 700 "$RUNTIME_DIR"
  (
    umask 077
    cat > "$RUNTIME_ENV" <<EOF
# GENERATED by scripts/demo.sh for the LOCAL demo only. Secrets: never commit or share.
API_IMAGE=$API_IMAGE
WEB_IMAGE=$WEB_IMAGE
PRODUCT_WEB_PORT=$WEB_PORT
POSTGRES_DB=platform
POSTGRES_USER=platform
POSTGRES_PASSWORD=$pg_password
APP_ENVIRONMENT=local
APP_LOG_LEVEL=INFO
APP_PRODUCT_AUTH_MODE=api_key
APP_COMPANY_ID=$company
APP_PRODUCT_API_KEYS=$principals
APP_BUSINESS_BACKEND=mock
APP_DEFAULT_MODEL_PROVIDER=demo
APP_DEFAULT_MODEL_ID=
OS_SECURITY_KEY=$os_key
EOF
    cat > "$CREDENTIALS" <<EOF
# GENERATED by scripts/demo.sh: what you paste into the Operations Console.
PRODUCT_API_KEY=$product_key
STORE_ID=$store
DEMO_DATE=$DEMO_DATE
WEB_URL=$WEB_URL
EOF
  )
  chmod 600 "$RUNTIME_ENV" "$CREDENTIALS"
  say "generated fresh demo credentials in ${RUNTIME_DIR#"$ROOT"/}"
}

ensure_runtime() {
  if [[ -f "$RUNTIME_ENV" && -f "$CREDENTIALS" ]]; then
    say "reusing the existing demo credentials (./scripts/demo.sh reset starts over)"
    return
  fi
  [[ ! -e "$RUNTIME_ENV" && ! -e "$CREDENTIALS" ]] \
    || die "incomplete demo state in $RUNTIME_DIR; run ./scripts/demo.sh reset"
  generate_runtime
}

print_credentials() {
  [[ -f "$CREDENTIALS" ]] || die "no demo environment yet; run ./scripts/demo.sh up"
  cat <<EOF
Operations Console:
$(value_of "$CREDENTIALS" WEB_URL)

Product API Key:
$(value_of "$CREDENTIALS" PRODUCT_API_KEY)

Store UUID:
$(value_of "$CREDENTIALS" STORE_ID)

Demo business date:
$(value_of "$CREDENTIALS" DEMO_DATE)
EOF
}

cmd_up() {
  require_docker
  build_images
  ensure_runtime
  [[ "$(value_of "$RUNTIME_ENV" API_IMAGE)" == "$API_IMAGE" \
     && "$(value_of "$RUNTIME_ENV" WEB_IMAGE)" == "$WEB_IMAGE" \
     && "$(value_of "$RUNTIME_ENV" PRODUCT_WEB_PORT)" == "$WEB_PORT" ]] \
    || die "the demo was created with other images or another port; run ./scripts/demo.sh reset"
  say "validating the composed demo configuration"
  compose config --quiet
  [[ "$(compose config --services | sort | tr '\n' ' ')" == "api migrate postgres web " ]] \
    || die "unexpected demo services"
  say "starting postgres -> migrate -> api -> web (first start takes a minute)"
  compose up -d --wait --wait-timeout 300 web \
    || { compose ps -a >&2; die "the demo did not become healthy (see: docker compose -p $PROJECT logs)"; }
  local migrate_exit
  migrate_exit="$(docker inspect "$(compose ps -a -q migrate)" --format '{{.State.ExitCode}}')"
  [[ "$migrate_exit" == 0 ]] || die "the migration job exited $migrate_exit"
  printf '\nCommerce AI Product Demo is ready\n\n'
  print_credentials
  printf '\nSuggested analysis:\n%s\n\n' "$SUGGESTED"
  printf 'Stop: ./scripts/demo.sh down    Reset (deletes demo data): ./scripts/demo.sh reset\n'
}

cmd_down() {
  require_docker
  [[ -f "$RUNTIME_ENV" ]] || { say "no demo environment"; return; }
  compose down --remove-orphans
  say "stopped; the demo database is kept (./scripts/demo.sh up resumes it)"
}

cmd_reset() {
  require_docker
  if [[ -f "$RUNTIME_ENV" ]]; then
    compose down -v --remove-orphans
  fi
  rm -rf "$RUNTIME_DIR"
  say "demo stopped; its database volume and credentials were deleted"
}

cmd_status() {
  require_docker
  [[ -f "$RUNTIME_ENV" && -f "$CREDENTIALS" ]] \
    || { echo "Demo: not configured (run ./scripts/demo.sh up)"; return; }
  echo "Demo stack ($PROJECT):"
  compose ps --format 'table {{.Service}}\t{{.State}}\t{{.Status}}'
  local health="unreachable"
  if command -v curl >/dev/null 2>&1; then
    health="$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "$WEB_URL/api/product/health" || true)"
    [[ "$health" == 200 ]] && health="ok (200 through the Web BFF)"
  else
    health="unknown (curl not installed)"
  fi
  local key_state="not configured"
  [[ -n "$(value_of "$CREDENTIALS" PRODUCT_API_KEY)" ]] && key_state="configured (./scripts/demo.sh credentials)"
  cat <<EOF

Operations Console: $WEB_URL
Product API health: $health
Product API key:    $key_state
Store UUID:         $(value_of "$CREDENTIALS" STORE_ID)
Demo business date: $(value_of "$CREDENTIALS" DEMO_DATE)
EOF
}

case "${1:-}" in
  up) cmd_up ;;
  down) cmd_down ;;
  reset) cmd_reset ;;
  status) cmd_status ;;
  credentials) print_credentials ;;
  *) echo "usage: $0 up|down|reset|status|credentials" >&2; exit 2 ;;
esac
