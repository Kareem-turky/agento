"""Task 039: static guards for operator backup / restore tooling and deployment wiring.

The runtime behaviour is proven by .github/scripts/backup-restore-drill.sh and the
Task 039 steps of .github/scripts/deployment-smoke.sh in the Infrastructure CI job.
"""

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = ROOT / "deployments" / "template"
BACKUP = TEMPLATE / "ops" / "backup.sh"
RESTORE = TEMPLATE / "ops" / "restore-into-empty.sh"
DRILL = ROOT / ".github" / "scripts" / "backup-restore-drill.sh"
SMOKE = ROOT / ".github" / "scripts" / "deployment-smoke.sh"
CI = ROOT / ".github" / "workflows" / "ci.yml"


def code(path: Path) -> str:
    return "\n".join(
        line for line in path.read_text().splitlines() if not line.lstrip().startswith("#")
    )


def test_scripts_are_strict_private_and_executable() -> None:
    for path in (BACKUP, RESTORE, DRILL):
        source = path.read_text()
        assert source.startswith("#!/usr/bin/env bash\n")
        assert "set -euo pipefail" in source
        assert path.stat().st_mode & 0o111, path.name
    for path in (BACKUP, RESTORE):
        assert "umask 077" in code(path)


def test_backup_is_a_full_online_custom_format_dump_published_atomically() -> None:
    backup = code(BACKUP)
    dump = re.findall(r"pg_dump --[^']*", backup)
    assert dump == ['pg_dump --format=custom --no-password -U "$POSTGRES_USER" -d "$POSTGRES_DB"']
    for narrowing in (" -n ", "--schema", " -t ", "--table", "--data-only", "--schema-only"):
        assert narrowing not in dump[0], narrowing  # the WHOLE database, never product.* only
    assert 'mktemp "$DIR/.agento-backup.XXXXXX"' in backup  # same filesystem: atomic rename
    assert 'mv -n "$TMP" "$OUTPUT"' in backup and 'mv -n "$TMP_SUM" "$CHECKSUM"' in backup
    assert backup.index('mv -n "$TMP" "$OUTPUT"') < backup.index('mv -n "$TMP_SUM" "$CHECKSUM"')
    assert "refusing to overwrite an existing backup" in backup
    assert "trap cleanup EXIT" in backup and 'rm -f "$TMP" "$TMP_SUM"' in backup
    assert 'chmod 600 "$TMP"' in backup and "sha256sum" in backup
    assert '"PGDMP"' in backup  # verified before publication


def test_backup_and_restore_never_print_transfer_or_copy_secrets() -> None:
    for path in (BACKUP, RESTORE):
        source = code(path)
        for word in (
            "POSTGRES_PASSWORD",
            "DATABASE_URL",
            "APP_PRODUCT_API_KEYS",
            "OS_SECURITY_KEY",
            "curl",
            "wget",
            "scp",
            "rsync",
            "aws ",
            "gsutil",
            "az ",
            "ssh ",
            "nc ",
            "SECRETS_DIR",
            "CONFIG_DIR",
            "set -x",
        ):
            assert word not in source, (path.name, word)
        assert "crontab" not in source and "while true" not in source  # no scheduler


def test_restore_refuses_before_changing_anything_and_never_replaces_live_data() -> None:
    restore = code(RESTORE)
    order = [
        restore.index('"PGDMP"'),
        restore.index("does not match its checksum"),
        restore.index("pg_isready"),
        restore.index("is running against this database"),
        restore.index("is not empty"),
        restore.index("pg_restore"),
        restore.index("compose run --rm --no-deps migrate ||"),
        restore.index("Product schema is not at the expected revision"),
    ]
    assert order == sorted(order)
    assert (
        "pg_restore --no-password --no-owner --no-acl --exit-on-error --single-transaction"
        in restore
    )
    # Commands only (the closing instruction to the operator mentions `docker compose up -d`).
    commands = "\n".join(
        line for line in restore.splitlines() if not line.lstrip().startswith(("echo ", "printf "))
    )
    for destructive in (
        "--clean",
        "--if-exists",
        "DROP ",
        "dropdb",
        "TRUNCATE",
        "--create",
        "down -v",
        " up ",
        "up -d",
    ):
        assert destructive not in commands, destructive
    assert "for service in api web migrate; do" in restore
    assert "alembic -c /app/alembic.ini heads" in restore  # verified against the image's head


def test_compose_adds_only_the_two_startup_settings_and_no_collector() -> None:
    compose = yaml.safe_load((TEMPLATE / "compose.yaml").read_text())
    assert set(compose["services"]) == {"postgres", "migrate", "api", "web"}
    api = compose["services"]["api"]["environment"]
    assert api["APP_OTEL_EXPORT_MODE"] == "${APP_OTEL_EXPORT_MODE:-disabled}"
    assert api["APP_OTEL_EXPORT_ENDPOINT"] == "${APP_OTEL_EXPORT_ENDPOINT:-}"
    assert not [name for name in api if name.startswith("OTEL_")]
    for service in compose["services"].values():
        assert "ports" not in service or service is compose["services"]["web"]
    text = (TEMPLATE / "compose.yaml").read_text().lower()
    for word in ("otel-collector", "otelcol", "jaeger", "prometheus", "grafana", "tempo", "loki"):
        assert word not in text, word


def test_ci_runs_the_drill_on_a_disposable_project_and_always_tears_it_down() -> None:
    workflow = yaml.safe_load(CI.read_text())
    steps = workflow["jobs"]["infrastructure"]["steps"]
    runs = [step.get("run", "") for step in steps]
    smoke = next(i for i, r in enumerate(runs) if "deployment-smoke.sh" in r)
    drill = next(i for i, r in enumerate(runs) if "backup-restore-drill.sh" in r)
    demo = next(i for i, r in enumerate(runs) if "demo-smoke.sh" in r)
    assert smoke < drill < demo
    assert any("cap-restore-drill down -v" in r for r in runs)
    source = DRILL.read_text()
    assert 'PROJECT="cap-restore-drill"' in source
    assert "trap cleanup EXIT" in source and "down -v --remove-orphans" in source
    for proof in (
        "agent-management/agent/disable",
        "online backup",
        "never overwrites",
        "failed backup leaves no file",
        "non-empty database",
        "does not match its checksum",
        "EMPTY database",
        "known state survived",
        "== 0008",
    ):
        assert proof in source, proof


def test_deployment_smoke_proves_outage_recovery_and_public_minimal_health() -> None:
    smoke = SMOKE.read_text()
    for proof in (
        'docker stop -t 10 "$pg_id"',
        '"$(api_internal "$api_id" /health/ready none)" == 503',
        "liveness failed during the outage",
        "BFF readiness not 503",
        "Web health check passed during the outage",
        'docker start "$pg_id"',
        "the API was restarted",
        '{"status":"alive"}',
        "System Status accepted the AgentOS key",
        '"event":"product.operation.completed"',
    ):
        assert proof in smoke, proof
