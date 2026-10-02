"""FilesystemIntegrationSecretStore: atomic replacement, path safety, no leaks."""

import asyncio
import os
import stat
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import SecretStr

from app.integration_management import SecretMaterialMissingError, SecretStoreError
from app.integration_management.filesystem_secrets import FilesystemIntegrationSecretStore

VALUE = "test-only-secret-value-ff21"  # noqa: S105 - test fixture


@pytest.fixture
def root(tmp_path: Path) -> Path:
    path = tmp_path / "integration-secrets"
    path.mkdir(mode=0o700)
    return path


def run(coro):
    return asyncio.run(coro)


def test_round_trip_names_and_permissions(root: Path) -> None:
    store = FilesystemIntegrationSecretStore(root)
    cid = uuid4()
    run(store.replace(cid, {"api_key": SecretStr(VALUE), "api_secret": SecretStr("b")}))
    assert run(store.field_names(cid)) == {"api_key", "api_secret"}
    read = run(store.read(cid, frozenset({"api_key"})))
    assert read["api_key"].get_secret_value() == VALUE and VALUE not in repr(read)
    files = list(root.iterdir())
    assert [f.name for f in files] == [f"{cid}.json"]
    assert stat.S_IMODE(files[0].stat().st_mode) == 0o600
    with pytest.raises(SecretMaterialMissingError):
        run(store.read(cid, frozenset({"missing"})))
    assert run(store.field_names(uuid4())) == frozenset()


def test_replace_is_atomic_and_keeps_the_old_set_on_failure(root: Path, monkeypatch) -> None:
    store = FilesystemIntegrationSecretStore(root)
    cid = uuid4()
    run(store.replace(cid, {"api_key": SecretStr("old")}))

    def broken(*args, **kwargs):
        raise OSError("disk full at /secret/path")

    monkeypatch.setattr(os, "replace", broken)
    with pytest.raises(SecretStoreError) as error:
        run(store.replace(cid, {"api_key": SecretStr("new")}))
    assert "/secret/path" not in str(error.value) and error.value.__cause__ is None
    monkeypatch.undo()
    assert run(store.read(cid, frozenset({"api_key"})))["api_key"].get_secret_value() == "old"
    assert [p.name for p in root.iterdir()] == [f"{cid}.json"]  # no temp file left behind
    run(store.replace(cid, {"api_key": SecretStr("new")}))
    assert run(store.read(cid, frozenset({"api_key"})))["api_key"].get_secret_value() == "new"


def test_delete_is_deterministic(root: Path) -> None:
    store = FilesystemIntegrationSecretStore(root)
    cid = uuid4()
    run(store.replace(cid, {"api_key": SecretStr(VALUE)}))
    run(store.delete(cid))
    run(store.delete(cid))  # idempotent
    assert list(root.iterdir()) == [] and run(store.field_names(cid)) == frozenset()


@pytest.mark.parametrize("bad", ["../escape", "/etc/passwd", "x", "", 7,
                                 "11111111-1111-1111-1111-111111111111/../x"])  # fmt: skip
def test_only_uuid_objects_become_file_names(root: Path, bad) -> None:
    store = FilesystemIntegrationSecretStore(root)
    for call in (lambda: store.replace(bad, {"k": SecretStr("v")}), lambda: store.delete(bad),
                 lambda: store.field_names(bad)):  # fmt: skip
        with pytest.raises(SecretStoreError):
            run(call())
    assert list(root.iterdir()) == []


def test_symlink_escape_is_refused(root: Path, tmp_path: Path) -> None:
    outside = tmp_path / "outside.json"
    outside.write_text('{"format": 1, "values": {"api_key": "planted"}}')
    store = FilesystemIntegrationSecretStore(root)
    cid = uuid4()
    (root / f"{cid}.json").symlink_to(outside)
    with pytest.raises(SecretStoreError):
        run(store.read(cid, frozenset({"api_key"})))
    with pytest.raises(SecretStoreError):
        run(store.field_names(cid))
    # Replacing renames over the LINK itself: the target outside the root is untouched.
    run(store.replace(cid, {"api_key": SecretStr(VALUE)}))
    assert outside.read_text() == '{"format": 1, "values": {"api_key": "planted"}}'
    assert not (root / f"{cid}.json").is_symlink()
    run(store.delete(cid))
    assert outside.exists()


def test_root_must_be_an_absolute_private_real_directory(root: Path, tmp_path: Path) -> None:
    with pytest.raises(SecretStoreError):
        FilesystemIntegrationSecretStore("relative/secrets")
    with pytest.raises(SecretStoreError):
        FilesystemIntegrationSecretStore(tmp_path / "missing")
    link = tmp_path / "linked-root"
    link.symlink_to(root)
    with pytest.raises(SecretStoreError):
        FilesystemIntegrationSecretStore(link)
    file_root = tmp_path / "file"
    file_root.write_text("x")
    with pytest.raises(SecretStoreError):
        FilesystemIntegrationSecretStore(file_root)
    open_root = tmp_path / "open"
    open_root.mkdir()
    open_root.chmod(0o777)
    with pytest.raises(SecretStoreError):
        FilesystemIntegrationSecretStore(open_root)


def test_a_root_swapped_for_a_symlink_later_is_refused(root: Path, tmp_path: Path) -> None:
    store = FilesystemIntegrationSecretStore(root)
    moved = tmp_path / "moved"
    root.rename(moved)
    root.symlink_to(moved)
    with pytest.raises(SecretStoreError):
        run(store.replace(uuid4(), {"k": SecretStr(VALUE)}))
    assert list(moved.iterdir()) == []


def test_corrupted_material_fails_closed_without_leaking(root: Path) -> None:
    store = FilesystemIntegrationSecretStore(root)
    cid = uuid4()
    (root / f"{cid}.json").write_text(f'{{"format": 2, "values": {{"k": "{VALUE}"}}}}')
    with pytest.raises(SecretStoreError) as error:
        run(store.read(cid, frozenset({"k"})))
    assert VALUE not in str(error.value) and str(root) not in str(error.value)
