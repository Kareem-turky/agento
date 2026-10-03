#!/usr/bin/env bash
# Operator-run restore of a full Agento database backup into an EMPTY database (Task 039).
#
#   deployments/template/ops/restore-into-empty.sh [--project NAME] [--env-file FILE] BACKUP.dump
#
# This is NOT a destructive "replace the live database" tool. It refuses (before changing
# anything) unless ALL of these hold:
#   - the backup exists, is non-empty and is a PostgreSQL custom-format dump;
#   - if BACKUP.dump.sha256 exists, the backup matches it (verified BEFORE any database access);
#   - the installation's PostgreSQL is running and reachable;
#   - the Product API, the Web and the migration job are NOT running against it;
#   - the target database holds no Product, Agno runtime or other user schema or table.
#
# Then it restores the whole dump in ONE transaction (`pg_restore --single-transaction
# --exit-on-error --no-owner --no-acl`), runs the explicit Product migration
# (`alembic upgrade head`, the normal migration job of the same API image; an older valid
# backup is upgraded), and verifies the Product schema is at the image's head revision.
# It never touches Agno's schema through Alembic, never prints a secret, and does NOT
# start the Product: start it yourself afterwards (`docker compose up -d`).
#
# Integration/backend secrets and configuration are not in the database: restore them
# from your separately protected copies (see docs/PRODUCTION_OPERATIONS.md).
set -euo pipefail
umask 077

HERE="$(cd "$(dirname "$0")" && pwd)"
COMPOSE_FILE="$HERE/../compose.yaml"
ENV_FILE="$HERE/../.env"
PROJECT=""

fail() { printf 'restore: REFUSED: %s\n' "$*" >&2; exit 1; }
usage() { printf 'usage: restore-into-empty.sh [--project NAME] [--env-file FILE] BACKUP.dump\n' >&2; exit 2; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --project) [[ $# -ge 2 ]] || usage; PROJECT="$2"; shift 2 ;;
    --env-file) [[ $# -ge 2 ]] || usage; ENV_FILE="$2"; shift 2 ;;
    --*) usage ;;
    *) break ;;
  esac
done
[[ $# -eq 1 ]] || usage
BACKUP="$1"
CHECKSUM="$BACKUP.sha256"
[[ -f "$ENV_FILE" ]] || fail "environment file not found"

compose() {
  docker compose ${PROJECT:+-p "$PROJECT"} -f "$COMPOSE_FILE" --env-file "$ENV_FILE" "$@"
}
psql_value() {
  compose exec -T postgres sh -c \
    'exec psql --no-password -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1 -tAc "$1"' \
    sh "$1"
}

# 1. The backup itself (nothing touches the database yet).
[[ -f "$BACKUP" && -s "$BACKUP" ]] || fail "backup file missing or empty"
[[ "$(head -c 5 "$BACKUP")" == "PGDMP" ]] || fail "not a PostgreSQL custom-format dump"
if [[ -e "$CHECKSUM" ]]; then
  expected="$(cut -d' ' -f1 < "$CHECKSUM")"
  actual="$(sha256sum "$BACKUP" | cut -d' ' -f1)"
  [[ "$expected" =~ ^[0-9a-f]{64}$ && "$expected" == "$actual" ]] \
    || fail "backup does not match its checksum; nothing was changed"
  echo "checksum verified"
else
  echo "warning: no checksum file next to the backup; integrity cannot be verified" >&2
fi

# 2. The target: reachable, not being served, and empty.
compose exec -T postgres sh -c 'exec pg_isready -q -U "$POSTGRES_USER" -d "$POSTGRES_DB"' \
  || fail "the installation's PostgreSQL is not running or not reachable"
running="$(compose ps --status running --services 2>/dev/null | tr '\n' ' ')"
for service in api web migrate; do
  [[ " $running " != *" $service "* ]] \
    || fail "'$service' is running against this database; stop api and web first (docker compose stop web api)"
done
schemas="$(psql_value "SELECT count(*) FROM pg_namespace
  WHERE nspname NOT LIKE 'pg\\_%' AND nspname NOT IN ('information_schema', 'public')")"
tables="$(psql_value "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
  WHERE c.relkind IN ('r', 'p', 'v', 'm', 'S', 'f') AND n.nspname = 'public'")"
[[ "$schemas" == 0 && "$tables" == 0 ]] \
  || fail "the target database is not empty (Product, Agno runtime or other data exists); nothing was changed"

# 3. Restore everything in one transaction (all or nothing).
compose exec -T postgres sh -c \
  'exec pg_restore --no-password --no-owner --no-acl --exit-on-error --single-transaction -U "$POSTGRES_USER" -d "$POSTGRES_DB"' \
  < "$BACKUP" || fail "pg_restore failed; the transaction was rolled back"
echo "database restored"

# 4. The explicit Product migration (an older valid backup is upgraded), then verify.
compose run --rm --no-deps migrate || fail "the Product migration failed after the restore"
head="$(compose run --rm --no-deps migrate alembic -c /app/alembic.ini heads 2>/dev/null \
        | sed -n 's/^\([0-9a-z_]*\) (head)$/\1/p')"
current="$(psql_value "SELECT string_agg(version_num, ',') FROM product.alembic_version")"
[[ -n "$head" && "$current" == "$head" ]] || fail "Product schema is not at the expected revision after migration"
echo "Product schema at revision $current"
echo "restore complete. Start the Product explicitly when ready: docker compose up -d"
