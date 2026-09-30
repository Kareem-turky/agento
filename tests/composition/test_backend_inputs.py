"""Task 025: backend input names, specs, opaque secret values and the read-only
filesystem input source."""

import copy
import os
import pickle
import stat
from dataclasses import FrozenInstanceError
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from app.composition import backend_inputs as inputs_module
from app.composition.backend_inputs import (
    EMPTY_INPUT_SPEC,
    MAX_BACKEND_INPUT_BYTES,
    BusinessBackendInputError,
    BusinessBackendInputInvalidError,
    BusinessBackendInputs,
    BusinessBackendInputSource,
    BusinessBackendInputSpec,
    BusinessBackendInputUnavailableError,
    FilesystemBusinessBackendInputSource,
    SecretValue,
    validate_backend_input_name,
)
from app.config import Settings

SECRET_MARKER = b"BACKEND-SECRET-MARKER-7f3a"
CONFIG_MARKER = "BACKEND-CONFIG-MARKER-51c2"
PATH_MARKER = "BACKEND-PATH-MARKER-9d04"
MARKERS = ("BACKEND-SECRET-MARKER-", "BACKEND-CONFIG-MARKER-", "BACKEND-PATH-MARKER-")

SPEC = BusinessBackendInputSpec(
    config_keys=frozenset({"BASE_URL", "ACCOUNT_ID"}), secret_keys=frozenset({"API_TOKEN"})
)


def make_settings(**values: Any) -> Settings:
    return Settings(_env_file=None, **values)  # pyright: ignore[reportCallIssue]


@pytest.fixture
def roots(tmp_path: Path) -> tuple[Path, Path]:
    config = tmp_path / f"config-{PATH_MARKER}"
    secrets = tmp_path / f"secrets-{PATH_MARKER}"
    config.mkdir()
    secrets.mkdir()
    return config, secrets


def populate(config: Path, secrets: Path) -> None:
    (config / "BASE_URL").write_text(f"https://api.example.com/{CONFIG_MARKER}")
    (config / "ACCOUNT_ID").write_text(f" {CONFIG_MARKER} \n")
    (secrets / "API_TOKEN").write_bytes(SECRET_MARKER + b"\n")


def assert_safe(error: BaseException) -> None:
    """Fixed code only: no marker, path, name or OS error text; no chained error."""
    text = f"{error!s} {error!r} {error.args!r}"
    for marker in (*MARKERS, "API_TOKEN", "BASE_URL", "ACCOUNT_ID", "/", "Errno", "No such"):
        assert marker not in text, marker
    assert error.__cause__ is None and error.__suppress_context__
    assert error.__context__ is None


# ----- names ------------------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["A", "API_TOKEN", "BASE_URL", "X1", "A_B_C", "A" * 64])
def test_valid_input_names(name: str) -> None:
    assert validate_backend_input_name(name) == name


@pytest.mark.parametrize(
    "name",
    ["", "a", "api_token", "Api_Token", "1ABC", "_ABC", "A-B", "A.B", "A/B", "../A", "A B",
     " A", "A ", "A\n", "A\x00", "A" * 65, "É", "A$", None, 1, b"API_TOKEN"],
)  # fmt: skip
def test_invalid_input_names_are_rejected_without_normalization(name) -> None:
    with pytest.raises(ValueError):
        validate_backend_input_name(name)


# ----- spec -------------------------------------------------------------------------------------


def test_spec_is_frozen_names_only_and_empty_by_default() -> None:
    assert EMPTY_INPUT_SPEC == BusinessBackendInputSpec()
    assert EMPTY_INPUT_SPEC.config_keys == frozenset() == EMPTY_INPUT_SPEC.secret_keys
    assert EMPTY_INPUT_SPEC.is_empty and not SPEC.is_empty
    with pytest.raises(FrozenInstanceError):
        SPEC.config_keys = frozenset()  # type: ignore[misc]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"config_keys": {"A"}}, {"secret_keys": ["A"]}, {"config_keys": ("A",)},
        {"config_keys": frozenset({"a"})}, {"secret_keys": frozenset({"A-B"})},
        {"config_keys": frozenset({"A"}), "secret_keys": frozenset({"A"})},
        {"secret_keys": frozenset({1})},
    ],
)  # fmt: skip
def test_invalid_specs_are_rejected(kwargs) -> None:
    with pytest.raises(ValueError):
        BusinessBackendInputSpec(**kwargs)


# ----- SecretValue ------------------------------------------------------------------------------


def test_secret_value_is_opaque_and_redacted() -> None:
    secret = SecretValue(SECRET_MARKER + b"\n")
    assert secret.reveal_bytes() == SECRET_MARKER + b"\n"  # exact bytes, not stripped
    for shown in (str(secret), repr(secret), f"{secret}", f"{secret!r}", format(secret, ">40"),
                  repr([secret]), repr({"k": secret}), str.format("{}", secret)):  # fmt: skip
        assert "BACKEND-SECRET-MARKER-" not in shown
    assert repr(secret) == "SecretValue(<redacted>)" == str(secret)
    assert not hasattr(secret, "__dict__")
    for attribute in ("decode", "json", "to_json", "model_dump", "get_secret_value"):
        assert not hasattr(secret, attribute), attribute


def test_secret_value_is_immutable_unhashable_and_not_serializable() -> None:
    secret = SecretValue(SECRET_MARKER)
    with pytest.raises(AttributeError):
        secret._value = b"x"  # type: ignore[misc]
    with pytest.raises(AttributeError):
        del secret._value
    with pytest.raises(TypeError):
        hash(secret)
    with pytest.raises(TypeError):
        pickle.dumps(secret)
    with pytest.raises(TypeError):
        copy.deepcopy(secret)
    assert secret == SecretValue(SECRET_MARKER) and secret != SecretValue(b"other")
    assert secret != SECRET_MARKER  # never equal to the raw bytes


@pytest.mark.parametrize("value", ["text", bytearray(b"x"), memoryview(b"x"), None, 1])
def test_secret_value_requires_bytes(value) -> None:
    with pytest.raises(ValueError):
        SecretValue(value)


def test_empty_secret_value_is_allowed() -> None:
    assert SecretValue(b"").reveal_bytes() == b""


# ----- BusinessBackendInputs --------------------------------------------------------------------


def test_inputs_are_read_only_and_repr_shows_counts_only() -> None:
    inputs = BusinessBackendInputs(
        config={"BASE_URL": CONFIG_MARKER, "ACCOUNT_ID": CONFIG_MARKER},
        secrets={"API_TOKEN": SecretValue(SECRET_MARKER)},
    )
    assert repr(inputs) == "BusinessBackendInputs(config_keys=2, secret_keys=1)" == str(inputs)
    assert inputs.config["BASE_URL"] == CONFIG_MARKER
    assert inputs.secrets["API_TOKEN"].reveal_bytes() == SECRET_MARKER
    assert inputs.matches(SPEC) and not inputs.is_empty
    with pytest.raises(TypeError):
        inputs.config["BASE_URL"] = "x"  # type: ignore[index]
    with pytest.raises(TypeError):
        inputs.secrets["NEW"] = SecretValue(b"x")  # type: ignore[index]
    with pytest.raises(AttributeError):
        inputs._config = {}  # type: ignore[misc]
    with pytest.raises(AttributeError):
        inputs.config = {}  # type: ignore[misc]
    with pytest.raises(TypeError):
        pickle.dumps(inputs)
    assert BusinessBackendInputs().is_empty
    assert repr(BusinessBackendInputs()) == "BusinessBackendInputs(config_keys=0, secret_keys=0)"


def test_inputs_copy_their_arguments() -> None:
    config = {"BASE_URL": "a"}
    inputs = BusinessBackendInputs(config=config)
    config["BASE_URL"] = "b"
    config["OTHER"] = "c"
    assert dict(inputs.config) == {"BASE_URL": "a"}


@pytest.mark.parametrize(
    ("config", "secrets"),
    [
        ({"base_url": "x"}, {}), ({"BASE_URL": b"x"}, {}), ({"BASE_URL": 1}, {}),
        ({}, {"API_TOKEN": b"raw"}), ({}, {"API_TOKEN": "raw"}), ({}, {"bad": SecretValue(b"")}),
        ({"A": "x"}, {"A": SecretValue(b"x")}),
    ],
)  # fmt: skip
def test_invalid_inputs_are_rejected(config, secrets) -> None:
    with pytest.raises(ValueError):
        BusinessBackendInputs(config=config, secrets=secrets)


def test_matches_requires_the_exact_declared_names() -> None:
    full = {"BASE_URL": "a", "ACCOUNT_ID": "b"}
    token = {"API_TOKEN": SecretValue(b"t")}
    assert BusinessBackendInputs(config=full, secrets=token).matches(SPEC)
    assert not BusinessBackendInputs(config={"BASE_URL": "a"}, secrets=token).matches(SPEC)
    assert not BusinessBackendInputs(config=full | {"EXTRA": "c"}, secrets=token).matches(SPEC)
    assert not BusinessBackendInputs(config=full).matches(SPEC)
    # A config name offered as a secret (or vice versa) does not match either.
    swapped = BusinessBackendInputs(config={"BASE_URL": "a", "API_TOKEN": "t"},
                                    secrets={"ACCOUNT_ID": SecretValue(b"b")})  # fmt: skip
    assert not swapped.matches(SPEC)


# ----- errors -----------------------------------------------------------------------------------


def test_error_hierarchy_and_fixed_codes() -> None:
    assert issubclass(BusinessBackendInputUnavailableError, BusinessBackendInputError)
    assert issubclass(BusinessBackendInputInvalidError, BusinessBackendInputError)
    assert str(BusinessBackendInputUnavailableError()) == "business_backend_input_unavailable"
    assert str(BusinessBackendInputInvalidError()) == "business_backend_input_invalid"


# ----- filesystem source ------------------------------------------------------------------------


def test_filesystem_source_reads_exactly_the_declared_files(roots) -> None:
    config, secrets = roots
    populate(config, secrets)
    (config / "UNDECLARED").write_text("never read")
    (secrets / "OTHER_TOKEN").write_bytes(b"never read")
    source = FilesystemBusinessBackendInputSource(config, secrets)
    assert isinstance(source, BusinessBackendInputSource)
    inputs = source.load(SPEC)
    assert dict(inputs.config) == {
        "BASE_URL": f"https://api.example.com/{CONFIG_MARKER}",
        "ACCOUNT_ID": f" {CONFIG_MARKER} \n",  # exact: nothing stripped or normalized
    }
    assert set(inputs.secrets) == {"API_TOKEN"}
    assert inputs.secrets["API_TOKEN"].reveal_bytes() == SECRET_MARKER + b"\n"  # exact bytes
    assert inputs.matches(SPEC)


def test_filesystem_source_repr_hides_paths(roots) -> None:
    source = FilesystemBusinessBackendInputSource(*roots)
    assert repr(source) == "FilesystemBusinessBackendInputSource()" == str(source)
    with pytest.raises(AttributeError):
        source._config_dir = None  # type: ignore[misc]


def test_empty_spec_reads_nothing_and_needs_no_roots(monkeypatch) -> None:
    opened: list[Any] = []
    monkeypatch.setattr(os, "open", lambda *a, **k: opened.append(a))
    inputs = FilesystemBusinessBackendInputSource(None, None).load(EMPTY_INPUT_SPEC)
    assert inputs.is_empty and opened == []


def test_only_the_declared_paths_are_opened(roots, monkeypatch) -> None:
    config, secrets = roots
    populate(config, secrets)
    opened: list[str] = []
    real_open = os.open

    def recording_open(path, flags, *args, **kwargs):
        opened.append(os.fspath(path))
        assert flags & os.O_NOFOLLOW and flags & os.O_CLOEXEC
        assert not flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND)
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", recording_open)
    FilesystemBusinessBackendInputSource(config, secrets).load(SPEC)
    assert sorted(opened) == sorted([str(config / "ACCOUNT_ID"), str(config / "BASE_URL"),
                                     str(secrets / "API_TOKEN")])  # fmt: skip


def test_empty_files_are_allowed(roots) -> None:
    config, secrets = roots
    (config / "BASE_URL").write_text("")
    (secrets / "API_TOKEN").write_bytes(b"")
    spec = BusinessBackendInputSpec(frozenset({"BASE_URL"}), frozenset({"API_TOKEN"}))
    inputs = FilesystemBusinessBackendInputSource(config, secrets).load(spec)
    assert inputs.config["BASE_URL"] == ""
    assert inputs.secrets["API_TOKEN"].reveal_bytes() == b""


def test_secret_bytes_are_never_decoded(roots) -> None:
    config, secrets = roots
    raw = b"\xff\x00\xfe" + SECRET_MARKER + b"\r\n"
    (secrets / "API_TOKEN").write_bytes(raw)
    spec = BusinessBackendInputSpec(secret_keys=frozenset({"API_TOKEN"}))
    inputs = FilesystemBusinessBackendInputSource(None, secrets).load(spec)
    assert inputs.secrets["API_TOKEN"].reveal_bytes() == raw


@pytest.mark.parametrize("size", [MAX_BACKEND_INPUT_BYTES - 1, MAX_BACKEND_INPUT_BYTES])
def test_files_up_to_the_cap_are_accepted(roots, size) -> None:
    config, secrets = roots
    (config / "BASE_URL").write_text("a" * size)
    (secrets / "API_TOKEN").write_bytes(b"s" * size)
    spec = BusinessBackendInputSpec(frozenset({"BASE_URL"}), frozenset({"API_TOKEN"}))
    inputs = FilesystemBusinessBackendInputSource(config, secrets).load(spec)
    assert len(inputs.config["BASE_URL"]) == size
    assert len(inputs.secrets["API_TOKEN"].reveal_bytes()) == size


@pytest.mark.parametrize("kind", ["config", "secret"])
def test_oversized_files_are_rejected_with_a_bounded_read(roots, monkeypatch, kind) -> None:
    config, secrets = roots
    root, spec = (
        (config, BusinessBackendInputSpec(config_keys=frozenset({"BASE_URL"})))
        if kind == "config"
        else (secrets, BusinessBackendInputSpec(secret_keys=frozenset({"BASE_URL"})))
    )
    with (root / "BASE_URL").open("wb") as handle:
        handle.truncate(50 * 1024 * 1024)  # sparse: never fully read
    read_total = 0
    real_read = os.read

    def counting_read(fd, n):
        nonlocal read_total
        data = real_read(fd, n)
        read_total += len(data)
        return data

    monkeypatch.setattr(os, "read", counting_read)
    with pytest.raises(BusinessBackendInputInvalidError) as caught:
        FilesystemBusinessBackendInputSource(config, secrets).load(spec)
    assert read_total <= MAX_BACKEND_INPUT_BYTES + 1
    assert_safe(caught.value)


@pytest.mark.parametrize(
    "content",
    [
        b"\xff\xfe" + CONFIG_MARKER.encode(),
        b"a\x00b",
        b"\xc3",
        CONFIG_MARKER.encode() + b"\xed\xa0\x80",
    ],
)
def test_config_must_be_strict_utf8_without_nul(roots, content) -> None:
    config, _ = roots
    (config / "BASE_URL").write_bytes(content)
    spec = BusinessBackendInputSpec(config_keys=frozenset({"BASE_URL"}))
    with pytest.raises(BusinessBackendInputInvalidError) as caught:
        FilesystemBusinessBackendInputSource(config, None).load(spec)
    assert_safe(caught.value)


def test_config_with_a_bom_is_kept_exactly(roots) -> None:
    config, _ = roots
    (config / "BASE_URL").write_bytes("﻿value\r\n".encode())
    spec = BusinessBackendInputSpec(config_keys=frozenset({"BASE_URL"}))
    inputs = FilesystemBusinessBackendInputSource(config, None).load(spec)
    assert inputs.config["BASE_URL"] == "﻿value\r\n"


def test_missing_files_are_unavailable(roots) -> None:
    with pytest.raises(BusinessBackendInputUnavailableError) as caught:
        FilesystemBusinessBackendInputSource(*roots).load(SPEC)
    assert_safe(caught.value)


def test_missing_or_unset_roots_are_unavailable(tmp_path) -> None:
    missing = tmp_path / f"absent-{PATH_MARKER}"
    for source in (FilesystemBusinessBackendInputSource(missing, missing),
                   FilesystemBusinessBackendInputSource(None, None)):  # fmt: skip
        with pytest.raises(BusinessBackendInputUnavailableError) as caught:
            source.load(SPEC)
        assert_safe(caught.value)
    assert not missing.exists()  # never created


def test_a_root_that_is_a_file_is_unavailable(tmp_path) -> None:
    root = tmp_path / f"file-{PATH_MARKER}"
    root.write_text("x")
    spec = BusinessBackendInputSpec(config_keys=frozenset({"BASE_URL"}))
    with pytest.raises(BusinessBackendInputUnavailableError) as caught:
        FilesystemBusinessBackendInputSource(root, None).load(spec)
    assert_safe(caught.value)


@pytest.mark.parametrize("kind", ["config", "secret"])
def test_symlinked_input_files_are_rejected(roots, tmp_path, kind) -> None:
    config, secrets = roots
    target = tmp_path / f"target-{PATH_MARKER}"
    target.write_bytes(SECRET_MARKER)
    root = config if kind == "config" else secrets
    (root / "API_TOKEN").symlink_to(target)
    spec = (BusinessBackendInputSpec(config_keys=frozenset({"API_TOKEN"})) if kind == "config"
            else BusinessBackendInputSpec(secret_keys=frozenset({"API_TOKEN"})))  # fmt: skip
    with pytest.raises(BusinessBackendInputInvalidError) as caught:
        FilesystemBusinessBackendInputSource(config, secrets).load(spec)
    assert_safe(caught.value)


def test_dangling_symlinks_are_rejected(roots) -> None:
    _, secrets = roots
    (secrets / "API_TOKEN").symlink_to(secrets / "nowhere")
    spec = BusinessBackendInputSpec(secret_keys=frozenset({"API_TOKEN"}))
    with pytest.raises(BusinessBackendInputInvalidError) as caught:
        FilesystemBusinessBackendInputSource(None, secrets).load(spec)
    assert_safe(caught.value)


def test_directories_are_rejected(roots) -> None:
    _, secrets = roots
    (secrets / "API_TOKEN").mkdir()
    spec = BusinessBackendInputSpec(secret_keys=frozenset({"API_TOKEN"}))
    with pytest.raises(BusinessBackendInputInvalidError) as caught:
        FilesystemBusinessBackendInputSource(None, secrets).load(spec)
    assert_safe(caught.value)


def test_fifos_are_rejected_without_blocking(roots) -> None:
    _, secrets = roots
    os.mkfifo(secrets / "API_TOKEN")
    spec = BusinessBackendInputSpec(secret_keys=frozenset({"API_TOKEN"}))
    with pytest.raises(BusinessBackendInputInvalidError) as caught:
        FilesystemBusinessBackendInputSource(None, secrets).load(spec)
    assert_safe(caught.value)


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores file permissions")
def test_unreadable_files_are_unavailable(roots) -> None:
    _, secrets = roots
    path = secrets / "API_TOKEN"
    path.write_bytes(SECRET_MARKER)
    path.chmod(0)
    try:
        spec = BusinessBackendInputSpec(secret_keys=frozenset({"API_TOKEN"}))
        with pytest.raises(BusinessBackendInputUnavailableError) as caught:
            FilesystemBusinessBackendInputSource(None, secrets).load(spec)
        assert_safe(caught.value)
    finally:
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)


@pytest.mark.parametrize(
    "error",
    [
        PermissionError(13, "Permission denied", PATH_MARKER),
        OSError(5, "I/O error", PATH_MARKER),
        ValueError(PATH_MARKER),
    ],
)
def test_open_failures_are_unavailable_without_details(roots, monkeypatch, error) -> None:
    def failing_open(*args, **kwargs):
        raise error

    monkeypatch.setattr(os, "open", failing_open)
    with pytest.raises(BusinessBackendInputUnavailableError) as caught:
        FilesystemBusinessBackendInputSource(*roots).load(SPEC)
    assert_safe(caught.value)


def test_read_failures_are_unavailable_and_close_the_file(roots, monkeypatch) -> None:
    config, secrets = roots
    populate(config, secrets)
    closed: list[int] = []
    real_close = os.close

    def failing_read(fd, n):
        raise OSError(5, "I/O error " + PATH_MARKER)

    def recording_close(fd):
        closed.append(fd)
        real_close(fd)

    monkeypatch.setattr(os, "read", failing_read)
    monkeypatch.setattr(os, "close", recording_close)
    with pytest.raises(BusinessBackendInputUnavailableError) as caught:
        FilesystemBusinessBackendInputSource(config, secrets).load(SPEC)
    assert_safe(caught.value)
    assert len(closed) == 1


CLOSE_ERROR_TEXT = (
    f"close failed at /srv/{PATH_MARKER}/API_TOKEN holding {SECRET_MARKER.decode()} "
    f"{CONFIG_MARKER} BASE_URL"
)
RAW_CLOSE_MESSAGE = "close failed at"


def failing_close(record: list[int], error: BaseException | None = None):
    """Closes the real descriptor (no leak in the test process), records the attempt,
    then fails like a broken filesystem would."""
    real_close = os.close

    def close(fd: int) -> None:
        record.append(fd)
        real_close(fd)
        raise error if error is not None else OSError(5, CLOSE_ERROR_TEXT)

    return close


def assert_close_error_is_safe(error: BaseException) -> None:
    assert type(error) is BusinessBackendInputUnavailableError
    assert error.args == ("business_backend_input_unavailable",)
    text = f"{error!s} {error!r} {error.args!r}"
    for leaked in (*MARKERS, "API_TOKEN", "BASE_URL", "/", "Errno", RAW_CLOSE_MESSAGE,
                   "I/O error", "OSError"):  # fmt: skip
        assert leaked not in text, leaked
    assert error.__cause__ is None and error.__suppress_context__
    assert error.__context__ is None  # the close error is not attached


def secret_spec() -> BusinessBackendInputSpec:
    return BusinessBackendInputSpec(secret_keys=frozenset({"API_TOKEN"}))


def test_a_close_failure_after_a_successful_read_is_unavailable(roots, monkeypatch) -> None:
    _, secrets = roots
    (secrets / "API_TOKEN").write_bytes(SECRET_MARKER + b"\n")
    closes: list[int] = []
    monkeypatch.setattr(os, "close", failing_close(closes))
    with pytest.raises(BusinessBackendInputUnavailableError) as caught:
        FilesystemBusinessBackendInputSource(None, secrets).load(secret_spec())
    assert_close_error_is_safe(caught.value)
    assert len(closes) == 1  # attempted exactly once, never retried


@pytest.mark.parametrize(
    "error",
    [OSError(5, CLOSE_ERROR_TEXT), OSError(9, CLOSE_ERROR_TEXT, f"/srv/{PATH_MARKER}"),
     PermissionError(CLOSE_ERROR_TEXT), RuntimeError(CLOSE_ERROR_TEXT)],
)  # fmt: skip
def test_close_errors_never_leak_path_or_secret_markers(roots, monkeypatch, error) -> None:
    config, secrets = roots
    populate(config, secrets)
    closes: list[int] = []
    monkeypatch.setattr(os, "close", failing_close(closes, error))
    with pytest.raises(BusinessBackendInputError) as caught:
        FilesystemBusinessBackendInputSource(config, secrets).load(SPEC)
    assert_close_error_is_safe(caught.value)
    # The first declared file fails on close; nothing further is opened or closed.
    assert len(closes) == 1


def test_a_read_failure_and_a_close_failure_stay_one_safe_error(roots, monkeypatch) -> None:
    _, secrets = roots
    (secrets / "API_TOKEN").write_bytes(SECRET_MARKER)
    closes: list[int] = []

    def failing_read(fd, n):
        raise OSError(5, "read failed " + CLOSE_ERROR_TEXT)

    monkeypatch.setattr(os, "read", failing_read)
    monkeypatch.setattr(os, "close", failing_close(closes))
    with pytest.raises(BusinessBackendInputUnavailableError) as caught:
        FilesystemBusinessBackendInputSource(None, secrets).load(secret_spec())
    assert_close_error_is_safe(caught.value)
    assert len(closes) == 1


def test_an_invalid_input_stays_invalid_when_close_also_fails(roots, monkeypatch) -> None:
    _, secrets = roots
    (secrets / "API_TOKEN").write_bytes(b"s" * (MAX_BACKEND_INPUT_BYTES + 1))
    closes: list[int] = []
    monkeypatch.setattr(os, "close", failing_close(closes))
    with pytest.raises(BusinessBackendInputInvalidError) as caught:
        FilesystemBusinessBackendInputSource(None, secrets).load(secret_spec())
    error = caught.value
    assert error.args == ("business_backend_input_invalid",)
    assert error.__cause__ is None and error.__context__ is None
    assert len(closes) == 1


def test_an_unexpected_read_error_is_closed_and_translated(roots, monkeypatch) -> None:
    _, secrets = roots
    (secrets / "API_TOKEN").write_bytes(SECRET_MARKER)
    closes: list[int] = []
    real_close = os.close

    def recording_close(fd):
        closes.append(fd)
        real_close(fd)

    def broken_read(fd, n):
        raise RuntimeError(CLOSE_ERROR_TEXT)

    monkeypatch.setattr(os, "read", broken_read)
    monkeypatch.setattr(os, "close", recording_close)
    with pytest.raises(BusinessBackendInputUnavailableError) as caught:
        FilesystemBusinessBackendInputSource(None, secrets).load(secret_spec())
    assert_close_error_is_safe(caught.value)
    assert len(closes) == 1


def test_a_successful_load_closes_each_descriptor_exactly_once(roots, monkeypatch) -> None:
    config, secrets = roots
    populate(config, secrets)
    opened: list[int] = []
    closes: list[int] = []
    real_open, real_close = os.open, os.close

    def recording_open(*args, **kwargs):
        fd = real_open(*args, **kwargs)
        opened.append(fd)
        return fd

    def recording_close(fd):
        closes.append(fd)
        real_close(fd)

    monkeypatch.setattr(os, "open", recording_open)
    monkeypatch.setattr(os, "close", recording_close)
    inputs = FilesystemBusinessBackendInputSource(config, secrets).load(SPEC)
    assert len(opened) == 3 and sorted(closes) == sorted(opened)  # each exactly once
    assert dict(inputs.config) == {
        "BASE_URL": f"https://api.example.com/{CONFIG_MARKER}",
        "ACCOUNT_ID": f" {CONFIG_MARKER} \n",
    }
    assert inputs.secrets["API_TOKEN"].reveal_bytes() == SECRET_MARKER + b"\n"


def test_the_source_never_writes_or_changes_permissions(roots) -> None:
    config, secrets = roots
    populate(config, secrets)
    before = {p: (p.stat().st_mode, p.stat().st_mtime_ns) for r in roots for p in r.iterdir()}
    listing = {r: sorted(r.iterdir()) for r in roots}
    FilesystemBusinessBackendInputSource(config, secrets).load(SPEC)
    after = {p: (p.stat().st_mode, p.stat().st_mtime_ns) for r in roots for p in r.iterdir()}
    assert before == after and listing == {r: sorted(r.iterdir()) for r in roots}


def test_the_source_rejects_a_non_spec(roots) -> None:
    with pytest.raises(BusinessBackendInputInvalidError):
        FilesystemBusinessBackendInputSource(*roots).load({"config_keys": {"A"}})  # type: ignore[arg-type]


def test_the_source_does_not_log(roots, caplog) -> None:
    config, secrets = roots
    populate(config, secrets)
    caplog.set_level("DEBUG")
    FilesystemBusinessBackendInputSource(config, secrets).load(SPEC)
    with pytest.raises(BusinessBackendInputError):
        FilesystemBusinessBackendInputSource(None, None).load(SPEC)
    assert caplog.records == []


def test_the_cap_is_64_kib() -> None:
    assert MAX_BACKEND_INPUT_BYTES == inputs_module.MAX_BACKEND_INPUT_BYTES == 64 * 1024


# ----- settings -----------------------------------------------------------------------------------


def test_backend_directories_are_optional_paths() -> None:
    settings = make_settings()
    assert settings.backend_config_dir is None and settings.backend_secrets_dir is None
    for blank in ("", "   "):
        s = make_settings(backend_config_dir=blank, backend_secrets_dir=blank)
        assert s.backend_config_dir is None and s.backend_secrets_dir is None
    s = make_settings(backend_config_dir="/etc/backend/config",
                      backend_secrets_dir="/run/backend/secrets")  # fmt: skip
    assert s.backend_config_dir == Path("/etc/backend/config")
    assert s.backend_secrets_dir == Path("/run/backend/secrets")


def test_backend_directories_come_from_app_environment_variables(monkeypatch) -> None:
    monkeypatch.setenv("APP_BACKEND_CONFIG_DIR", "/etc/backend/config")
    monkeypatch.setenv("APP_BACKEND_SECRETS_DIR", "")
    s = make_settings()
    assert s.backend_config_dir == Path("/etc/backend/config")
    assert s.backend_secrets_dir is None


def test_settings_have_no_raw_backend_secret_fields() -> None:
    fields = set(Settings.model_fields)
    assert {"backend_config_dir", "backend_secrets_dir"} <= fields
    for forbidden in ("backend_api_token", "backend_password", "integration_secrets",
                      "integration_secrets_json", "backend_secrets", "backend_token"):  # fmt: skip
        assert forbidden not in fields, forbidden


def test_invalid_directory_values_are_rejected() -> None:
    with pytest.raises(ValidationError):
        make_settings(backend_config_dir=1.5)
