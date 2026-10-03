#!/usr/bin/env bash
# Operator-run, ONLINE, FULL PostgreSQL backup of one Agento installation (Task 039).
#
#   deployments/template/ops/backup.sh [--project NAME] [--env-file FILE] OUTPUT.dump
#
# Runs `pg_dump --format=custom` of the WHOLE installation database inside the private
# PostgreSQL container (Product schema, audit, approvals, workflows, conversations,
# Knowledge, Agent configuration, integration connection METADATA and the Agno runtime
# schema), while the Product keeps serving. Writes OUTPUT.dump and OUTPUT.dump.sha256.
#
# It does NOT copy secrets: integration credentials and backend secrets live OUTSIDE
# PostgreSQL by design (APP_INTEGRATION_SECRETS_DIR, APP_BACKEND_SECRETS_DIR), together with
# APP_BACKEND_CONFIG_DIR and the deployment .env. Protect those separately: they are part
# of a complete recovery set (see docs/PRODUCTION_OPERATIONS.md).
#
# Safety: umask 077 (files 0600); written to a temporary file in the target directory and
# renamed only after pg_dump succeeded and the dump was checked; an existing backup or
# checksum is never overwritten; a failed invocation leaves neither final artifact behind
# (it rolls back only what it published itself, never another invocation's files).
# Nothing is printed but file names and the checksum (never a password, URL or secret);
# nothing is uploaded.
# There is no scheduler: run it from your own operator tooling.
set -euo pipefail
umask 077

HERE="$(cd "$(dirname "$0")" && pwd)"
COMPOSE_FILE="$HERE/../compose.yaml"
ENV_FILE="$HERE/../.env"
PROJECT=""

fail() { printf 'backup: %s\n' "$*" >&2; exit 1; }
usage() { fail "usage: backup.sh [--project NAME] [--env-file FILE] OUTPUT.dump"; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --project) [[ $# -ge 2 ]] || usage; PROJECT="$2"; shift 2 ;;
    --env-file) [[ $# -ge 2 ]] || usage; ENV_FILE="$2"; shift 2 ;;
    --*) usage ;;
    *) break ;;
  esac
done
[[ $# -eq 1 ]] || usage
OUTPUT="$1"
CHECKSUM="$OUTPUT.sha256"
[[ -f "$ENV_FILE" ]] || fail "environment file not found"
DIR="$(dirname "$OUTPUT")"
NAME="$(basename "$OUTPUT")"
[[ -d "$DIR" ]] || fail "target directory does not exist"
[[ "$NAME" =~ ^[A-Za-z0-9._-]+$ ]] || fail "use a plain file name (letters, digits, . _ -)"
[[ ! -e "$OUTPUT" && ! -e "$CHECKSUM" ]] || fail "refusing to overwrite an existing backup"

compose() {
  docker compose ${PROJECT:+-p "$PROJECT"} -f "$COMPOSE_FILE" --env-file "$ENV_FILE" "$@"
}

TMP="$(mktemp "$DIR/.agento-backup.XXXXXX")"
TMP_SUM="$TMP.sha256"
# Explicit publication ownership: only artifacts THIS invocation published are ever rolled
# back, never a file that already existed (or that a concurrent backup published).
PUBLISHED_OUTPUT=0
PUBLISHED_CHECKSUM=0
SUCCEEDED=0
cleanup() {
  rm -f "$TMP" "$TMP_SUM"
  if [[ "$SUCCEEDED" != 1 ]]; then
    [[ "$PUBLISHED_CHECKSUM" == 1 ]] && rm -f "$CHECKSUM"
    [[ "$PUBLISHED_OUTPUT" == 1 ]] && rm -f "$OUTPUT"
  fi
  return 0
}
trap cleanup EXIT
trap 'exit 130' INT TERM HUP

# Inside the PostgreSQL container, over its local socket: no password is passed or shown.
if ! compose exec -T postgres sh -c \
     'exec pg_dump --format=custom --no-password -U "$POSTGRES_USER" -d "$POSTGRES_DB"' \
     > "$TMP"; then
  fail "pg_dump failed; no backup was written"
fi
[[ -s "$TMP" ]] || fail "pg_dump produced an empty file; no backup was written"
[[ "$(head -c 5 "$TMP")" == "PGDMP" ]] || fail "not a PostgreSQL custom-format dump; no backup was written"
chmod 600 "$TMP"

DIGEST="$(sha256sum "$TMP" | cut -d' ' -f1)"
printf '%s  %s\n' "$DIGEST" "$NAME" > "$TMP_SUM"
chmod 600 "$TMP_SUM"
# Atomic, no-clobber publication. Ownership is decided by whether OUR temporary file
# actually moved (not by mv's exit status, which differs between coreutils versions when a
# target already exists): a skipped move publishes nothing and owns nothing.
mv -n "$TMP" "$OUTPUT" 2>/dev/null || true
[[ ! -e "$TMP" ]] || fail "refusing to overwrite an existing backup (or it could not be published)"
PUBLISHED_OUTPUT=1
mv -n "$TMP_SUM" "$CHECKSUM" 2>/dev/null || true
[[ ! -e "$TMP_SUM" ]] || fail "the checksum could not be published; the backup was rolled back"
PUBLISHED_CHECKSUM=1
# Success only once BOTH final artifacts exist and verify; until then the trap rolls back.
(cd "$DIR" && sha256sum --check --quiet --strict -- "$NAME.sha256" >/dev/null 2>&1) \
  || fail "the published backup does not verify; it was rolled back"
SUCCEEDED=1

printf 'backup written: %s (%s bytes)\nsha256: %s\n' "$NAME" "$(stat -c %s "$OUTPUT")" "$DIGEST"
