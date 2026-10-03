"""Task 039: deterministic publication tests of deployments/template/ops/backup.sh.

No database and no timing race: TEST-ONLY wrappers on PATH stand in for ``docker`` (it
emits a fake custom-format dump) and, where a test needs a failure or a concurrent
contender at an exact step, for ``mv``. The real script runs unchanged.
"""

import hashlib
import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
BACKUP = ROOT / "deployments" / "template" / "ops" / "backup.sh"
REAL_MV = shutil.which("mv")
BASH = shutil.which("bash") or "/bin/bash"
DUMP = b"PGDMP" + b"\x01fake-custom-format-dump" * 64

pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None or shutil.which("sha256sum") is None or REAL_MV is None,
    reason="needs bash, coreutils",
)


def write_tool(directory: Path, name: str, body: str) -> None:
    path = directory / name
    path.write_text("#!/usr/bin/env bash\nset -u\n" + body)
    path.chmod(0o755)


@pytest.fixture
def sandbox(tmp_path: Path):
    tools = tmp_path / "bin"
    tools.mkdir()
    out = tmp_path / "backups"
    out.mkdir()
    env_file = tmp_path / "test.env"
    env_file.write_text("# test-only, no values\n")
    dump = tmp_path / "dump.bin"
    dump.write_bytes(DUMP)
    # docker compose ... exec ... pg_dump  ->  the fake dump on stdout (plus an optional hook)
    write_tool(tools, "docker", f'[[ -x "{tmp_path}/hook" ]] && "{tmp_path}/hook"\ncat "{dump}"\n')
    return tmp_path, tools, out, env_file


def run_backup(tools: Path, env_file: Path, target: Path) -> subprocess.CompletedProcess:
    env = {**os.environ, "PATH": f"{tools}:{os.environ['PATH']}"}
    return subprocess.run(  # noqa: S603 - the repository's own script, test-only PATH
        [BASH, str(BACKUP), "--env-file", str(env_file), str(target)],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


def leftovers(directory: Path) -> list[str]:
    return sorted(p.name for p in directory.iterdir() if p.name.startswith(".agento-backup."))


def test_a_successful_backup_publishes_both_verified_private_artifacts(sandbox) -> None:
    _, tools, out, env_file = sandbox
    target = out / "agento.dump"
    result = run_backup(tools, env_file, target)
    assert result.returncode == 0, result.stderr
    checksum = out / "agento.dump.sha256"
    assert target.read_bytes() == DUMP
    assert checksum.read_text() == f"{hashlib.sha256(DUMP).hexdigest()}  agento.dump\n"
    for path in (target, checksum):
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert leftovers(out) == []


def test_checksum_publication_failure_rolls_back_the_published_dump(sandbox) -> None:
    """The dump move succeeds, the checksum move fails: NEITHER artifact may remain."""
    tmp_path, tools, out, env_file = sandbox
    counter = tmp_path / "mv-calls"
    write_tool(
        tools,
        "mv",
        f'n=$(( $(cat "{counter}" 2>/dev/null || echo 0) + 1 ))\n'
        f'echo "$n" > "{counter}"\n'
        f'[[ "$n" -ge 2 ]] && exit 1\n'
        f'exec {REAL_MV} "$@"\n',
    )
    target = out / "agento.dump"
    result = run_backup(tools, env_file, target)
    assert counter.read_text().strip() == "2"  # the dump was published, then the checksum failed
    assert result.returncode != 0
    assert not target.exists() and not (out / "agento.dump.sha256").exists()
    assert leftovers(out) == []
    assert "rolled back" in result.stderr


def test_a_concurrent_winner_is_never_overwritten_or_removed(sandbox) -> None:
    """While this invocation dumps, another one publishes the same target (exact step)."""
    tmp_path, tools, out, env_file = sandbox
    target = out / "agento.dump"
    winner = b"PGDMP-the-winners-dump"
    winner_sum = f"{hashlib.sha256(winner).hexdigest()}  agento.dump\n"
    hook = tmp_path / "hook"
    hook.write_text(
        f"#!/usr/bin/env bash\nprintf '%s' '{winner.decode()}' > '{target}'\n"
        f"printf '%s' '{winner_sum}' > '{target}.sha256'\n"
    )
    hook.chmod(0o755)
    result = run_backup(tools, env_file, target)
    assert result.returncode != 0  # the loser fails
    assert target.read_bytes() == winner  # the winner's pair is untouched
    assert (out / "agento.dump.sha256").read_text() == winner_sum
    assert leftovers(out) == []


def test_a_winner_mid_publication_keeps_its_dump(sandbox) -> None:
    """The contender has published only its dump so far: the loser must not remove it."""
    tmp_path, tools, out, env_file = sandbox
    target = out / "agento.dump"
    hook = tmp_path / "hook"
    hook.write_text(f"#!/usr/bin/env bash\nprintf 'PGDMP-other' > '{target}'\n")
    hook.chmod(0o755)
    result = run_backup(tools, env_file, target)
    assert result.returncode != 0
    assert target.read_bytes() == b"PGDMP-other"
    assert not (out / "agento.dump.sha256").exists()  # the loser published nothing
    assert leftovers(out) == []


def test_existing_artifacts_are_refused_up_front_and_kept(sandbox) -> None:
    _, tools, out, env_file = sandbox
    target = out / "agento.dump"
    assert run_backup(tools, env_file, target).returncode == 0
    before = (target.read_bytes(), (out / "agento.dump.sha256").read_bytes())
    result = run_backup(tools, env_file, target)
    assert result.returncode != 0 and "refusing to overwrite" in result.stderr
    assert (target.read_bytes(), (out / "agento.dump.sha256").read_bytes()) == before


def test_a_failed_dump_leaves_nothing(sandbox) -> None:
    _, tools, out, env_file = sandbox
    write_tool(tools, "docker", "exit 1\n")
    result = run_backup(tools, env_file, out / "agento.dump")
    assert result.returncode != 0 and "pg_dump failed" in result.stderr
    assert sorted(p.name for p in out.iterdir()) == []
