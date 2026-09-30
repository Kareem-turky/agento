"""Backend inputs: the Product-owned boundary between deployment configuration and
backend plugins.

    deployment configuration (APP_BACKEND_CONFIG_DIR / APP_BACKEND_SECRETS_DIR)
      -> BusinessBackendInputSource.load(registration.input_spec)
      -> immutable BusinessBackendInputs (config: str values, secrets: SecretValue)
      -> the registered builder (which explicitly reveals what it needs)

A reviewed backend registration declares only the NAMES of the inputs it needs
(``BusinessBackendInputSpec``); the Product resolves them at startup, after the backend
is allowlisted and allowed in the environment. Builders and provider adapters never
scan the process environment, open secret files, parse ``.env`` or know how the
deployment stores credentials.

``FilesystemBusinessBackendInputSource`` is the initial source: for a declared name
``API_TOKEN`` it reads exactly ``<root>/API_TOKEN`` (no scanning, globbing, recursion,
extension guessing or fallback). ``BusinessBackendInputSource`` is the replaceable
boundary: a vault, cloud secret manager, HSM-backed service or orchestration secret
store can implement it later without changing domains, agents, workflows or backend
registrations. Inputs are resolved once at startup (no rotation or reload yet).

Secrets are opaque bytes (``SecretValue``): never decoded, stripped or parsed here,
never shown by ``str``/``repr``, revealed only by an explicit ``reveal_bytes()``. They
are minimized and redacted, and live as long as ordinary Python objects: guaranteed
memory zeroization is NOT provided (Python cannot guarantee it).

Nothing here logs, reads environment variables or reports paths, names or values in
errors: every error is a fixed code raised ``from None``.
"""

import errno
import hmac
import os
import re
import stat
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import NoReturn, Protocol, runtime_checkable

# An input NAME (never a value or a path): upper-case letters, digits, underscores.
BACKEND_INPUT_NAME_PATTERN = r"[A-Z][A-Z0-9_]{0,63}"
_INPUT_NAME = re.compile(BACKEND_INPUT_NAME_PATTERN)
# Fixed per-file cap for startup inputs (config and secrets alike).
MAX_BACKEND_INPUT_BYTES = 64 * 1024


def validate_backend_input_name(name: object) -> str:
    """Exact syntax check; nothing is trimmed, case-folded or otherwise normalized."""
    if not isinstance(name, str) or not _INPUT_NAME.fullmatch(name):
        raise ValueError("invalid business backend input name")
    return name


# ----- errors ---------------------------------------------------------------------------------


class BusinessBackendInputError(Exception):
    """Base of the backend-input errors. Fixed codes only: never a path, input name,
    value or operating-system error text."""

    code = "business_backend_input_error"

    def __init__(self) -> None:
        super().__init__(self.code)


class BusinessBackendInputUnavailableError(BusinessBackendInputError):
    """A declared input could not be obtained (no root configured, missing, unreadable)."""

    code = "business_backend_input_unavailable"


class BusinessBackendInputInvalidError(BusinessBackendInputError):
    """A declared input exists but is unsafe or malformed (symlink, not a regular file,
    too large, config that is not NUL-free UTF-8)."""

    code = "business_backend_input_invalid"


# ----- declarations ----------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class BusinessBackendInputSpec:
    """The input NAMES a backend registration requires. Never values or paths."""

    config_keys: frozenset[str] = frozenset()
    secret_keys: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        for keys in (self.config_keys, self.secret_keys):
            if not isinstance(keys, frozenset):
                raise ValueError("backend input keys must be a frozenset of names")
            for key in keys:
                validate_backend_input_name(key)
        if self.config_keys & self.secret_keys:
            raise ValueError("a backend input cannot be both config and secret")

    @property
    def is_empty(self) -> bool:
        return not self.config_keys and not self.secret_keys


EMPTY_INPUT_SPEC = BusinessBackendInputSpec()


# ----- values -----------------------------------------------------------------------------------


class SecretValue:
    """Opaque secret bytes. ``str``/``repr``/``format`` never reveal them; only
    ``reveal_bytes()`` does. Not picklable, not hashable, compared in constant time.
    Guaranteed memory zeroization is not provided."""

    __slots__ = ("_value",)

    def __init__(self, value: bytes) -> None:
        if type(value) is not bytes:
            raise ValueError("a secret value must be bytes")
        object.__setattr__(self, "_value", value)

    def reveal_bytes(self) -> bytes:
        """The exact secret bytes: call only where the credential is actually used."""
        return self._value

    def __repr__(self) -> str:
        return "SecretValue(<redacted>)"

    __str__ = __repr__

    def __format__(self, format_spec: str) -> str:
        return repr(self)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, SecretValue):
            return NotImplemented
        return hmac.compare_digest(self._value, other._value)

    __hash__ = None  # type: ignore[assignment]

    def __setattr__(self, name: str, value: object) -> NoReturn:
        raise AttributeError("a secret value is immutable")

    def __delattr__(self, name: str) -> NoReturn:
        raise AttributeError("a secret value is immutable")

    def __reduce__(self) -> NoReturn:
        raise TypeError("a secret value cannot be serialized")


class BusinessBackendInputs:
    """The resolved inputs handed to a backend builder: read-only ``config`` (exact
    text) and ``secrets`` (``SecretValue``). ``repr`` shows counts only, never names or
    values (config may be operationally sensitive too)."""

    __slots__ = ("_config", "_secrets")

    def __init__(
        self,
        config: Mapping[str, str] | None = None,
        secrets: Mapping[str, SecretValue] | None = None,
    ) -> None:
        config_items = dict(config or {})
        secret_items = dict(secrets or {})
        for key, value in config_items.items():
            validate_backend_input_name(key)
            if type(value) is not str:
                raise ValueError("backend config values must be str")
        for key, value in secret_items.items():
            validate_backend_input_name(key)
            if not isinstance(value, SecretValue):
                raise ValueError("backend secret values must be SecretValue")
        if config_items.keys() & secret_items.keys():
            raise ValueError("a backend input cannot be both config and secret")
        object.__setattr__(self, "_config", MappingProxyType(config_items))
        object.__setattr__(self, "_secrets", MappingProxyType(secret_items))

    @property
    def config(self) -> Mapping[str, str]:
        return self._config

    @property
    def secrets(self) -> Mapping[str, SecretValue]:
        return self._secrets

    @property
    def is_empty(self) -> bool:
        return not self._config and not self._secrets

    def matches(self, spec: BusinessBackendInputSpec) -> bool:
        """Exactly the declared names: nothing missing, nothing extra."""
        return (frozenset(self._config) == spec.config_keys
                and frozenset(self._secrets) == spec.secret_keys)  # fmt: skip

    def __repr__(self) -> str:
        return (f"BusinessBackendInputs(config_keys={len(self._config)}, "
                f"secret_keys={len(self._secrets)})")  # fmt: skip

    def __setattr__(self, name: str, value: object) -> NoReturn:
        raise AttributeError("business backend inputs are immutable")

    def __delattr__(self, name: str) -> NoReturn:
        raise AttributeError("business backend inputs are immutable")

    def __reduce__(self) -> NoReturn:
        raise TypeError("business backend inputs cannot be serialized")


# ----- sources ----------------------------------------------------------------------------------


@runtime_checkable
class BusinessBackendInputSource(Protocol):
    def load(self, spec: BusinessBackendInputSpec) -> BusinessBackendInputs:
        """Resolve exactly the declared inputs, or raise ``BusinessBackendInputError``."""
        ...


class FilesystemBusinessBackendInputSource:
    """Reads ``<config_dir>/<NAME>`` (UTF-8 text) and ``<secrets_dir>/<NAME>`` (opaque
    bytes) for the declared names only. READ ONLY: it never creates, lists, writes or
    changes permissions of anything. A root may be None when no key of that kind is
    declared."""

    __slots__ = ("_config_dir", "_secrets_dir")

    def __init__(self, config_dir: Path | None, secrets_dir: Path | None) -> None:
        object.__setattr__(self, "_config_dir", config_dir)
        object.__setattr__(self, "_secrets_dir", secrets_dir)

    def __repr__(self) -> str:
        return f"{type(self).__name__}()"

    def __setattr__(self, name: str, value: object) -> NoReturn:
        raise AttributeError("the backend input source is immutable")

    def load(self, spec: BusinessBackendInputSpec) -> BusinessBackendInputs:
        if not isinstance(spec, BusinessBackendInputSpec):
            raise BusinessBackendInputInvalidError from None
        config: dict[str, str] = {}
        for name in sorted(spec.config_keys):
            raw = _read_declared_file(self._config_dir, name)
            config[name] = _config_text(raw)
        secrets: dict[str, SecretValue] = {}
        for name in sorted(spec.secret_keys):
            secrets[name] = SecretValue(_read_declared_file(self._secrets_dir, name))
        return BusinessBackendInputs(config=config, secrets=secrets)


def _config_text(raw: bytes) -> str:
    """Exact UTF-8 text: no stripping, trimming, case changes or parsing."""
    text: str | None
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        text = None  # raised below, outside the handler: no chained error keeps the bytes
    if text is None or "\x00" in text:
        raise BusinessBackendInputInvalidError from None
    return text


def _read_declared_file(root: Path | None, name: str) -> bytes:
    """Read ``root/name`` safely: the declared name is the ONLY path component added,
    the final component is never followed if it is a symlink (``O_NOFOLLOW``), only a
    regular file is accepted, and at most ``MAX_BACKEND_INPUT_BYTES + 1`` bytes are read.

    Errors are raised outside the ``except`` blocks so no chained operating-system
    error (which would carry the path) stays attached to them."""
    if root is None:
        raise BusinessBackendInputUnavailableError from None
    if not isinstance(name, str) or not _INPUT_NAME.fullmatch(name):
        raise BusinessBackendInputInvalidError from None
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK
    failure: type[BusinessBackendInputError] | None = None
    descriptor = -1
    try:
        descriptor = os.open(os.path.join(os.fspath(root), name), flags)
    except OSError as error:
        # ELOOP: the input file itself is a symlink. Anything else: missing, unreadable...
        failure = (
            BusinessBackendInputInvalidError
            if error.errno == errno.ELOOP
            else BusinessBackendInputUnavailableError
        )
    except Exception:  # noqa: BLE001 - an unusable root never leaks details
        failure = BusinessBackendInputUnavailableError
    if failure is not None:
        raise failure from None
    # The descriptor is open: read, then attempt ONE close (no retry), whatever happened.
    # Neither a read nor a close failure escapes as a raw error: both become fixed
    # Product errors, raised below outside every handler so nothing is chained.
    data = b""
    closed = False
    try:
        try:
            data = _read_regular_file(descriptor)
        except BusinessBackendInputError as error:
            failure = type(error)
        except Exception:  # noqa: BLE001 - never leak raw read errors
            failure = BusinessBackendInputUnavailableError
    finally:
        closed = _close_once(descriptor)
    if failure is not None:
        raise failure from None
    if not closed:
        raise BusinessBackendInputUnavailableError from None
    return data


def _close_once(descriptor: int) -> bool:
    """Close exactly once; report failure instead of raising (the caller maps it to a
    fixed error). ``os.close`` is never retried: the descriptor state is unknown."""
    try:
        os.close(descriptor)
    except Exception:  # noqa: BLE001 - a raw close error may carry OS details
        return False
    return True


def _read_regular_file(descriptor: int) -> bytes:
    failure: type[BusinessBackendInputError] | None = None
    try:
        mode = os.fstat(descriptor).st_mode
    except OSError:
        mode = None
    if mode is None:
        raise BusinessBackendInputUnavailableError from None
    if not stat.S_ISREG(mode):  # directory, FIFO, device, socket, ...
        raise BusinessBackendInputInvalidError from None
    chunks: list[bytes] = []
    remaining = MAX_BACKEND_INPUT_BYTES + 1
    while remaining > 0:
        try:
            chunk = os.read(descriptor, remaining)
        except OSError:
            failure = BusinessBackendInputUnavailableError
            break
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    if failure is not None:
        raise failure from None
    data = b"".join(chunks)
    if len(data) > MAX_BACKEND_INPUT_BYTES:
        raise BusinessBackendInputInvalidError from None
    return data
