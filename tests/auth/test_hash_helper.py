"""The offline operator helper: reads the key from stdin, prints only the SHA-256."""

import hashlib
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = (
    Path(__file__).resolve().parents[2] / "apps" / "api" / "scripts" / "hash_product_api_key.py"
)
KEY = "test-helper-key-" + "q" * 32


def run_helper(stdin: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(  # noqa: S603 - fixed interpreter and script
        [sys.executable, str(SCRIPT), *args], input=stdin, capture_output=True, text=True,
        check=False,
    )  # fmt: skip


@pytest.mark.parametrize("stdin", [KEY, KEY + "\n", KEY + "\r\n"])
def test_prints_only_the_lowercase_sha256(stdin) -> None:
    result = run_helper(stdin)
    assert result.returncode == 0
    assert result.stdout == hashlib.sha256(KEY.encode("ascii")).hexdigest() + "\n"
    assert KEY not in result.stdout + result.stderr


@pytest.mark.parametrize(
    "stdin",
    ["", "short-key", "x" * 257, KEY + " ", " " + KEY, KEY[:-1] + "é", KEY + "\n\n",
     "has space " + "x" * 30],
)  # fmt: skip
def test_invalid_keys_are_refused_without_echoing_them(stdin) -> None:
    result = run_helper(stdin)
    assert result.returncode == 2 and result.stdout == ""
    if stdin.strip():
        assert stdin.strip() not in result.stderr


def test_never_accepts_the_secret_as_an_argument() -> None:
    result = run_helper("", KEY)
    assert result.returncode == 2 and result.stdout == ""
    assert KEY not in result.stderr
    result = run_helper(KEY, "--key", KEY)
    assert result.returncode == 2 and result.stdout == ""


def test_helper_shares_the_resolver_hash_function() -> None:
    source = SCRIPT.read_text()
    assert "from app.auth.keys import" in source and "hash_api_key" in source
    assert "argv" in source and "sys.argv[1:]" in source
    assert "print(digest)" in source
