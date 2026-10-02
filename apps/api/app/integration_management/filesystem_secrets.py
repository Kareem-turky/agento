"""``FilesystemIntegrationSecretStore``: integration secrets as files under ONE configured
root directory (``APP_INTEGRATION_SECRETS_DIR``), suited to the Product's isolated,
one-company-per-installation deployment.

    <root>/<connection uuid>.json      mode 0600, {"format": 1, "values": {name: value}}

Security properties:
- The root is configured explicitly (no default path), must be an absolute path to an
  existing real directory (not a symlink) that is not group/world-writable; it is
  re-checked on every operation.
- File names derive ONLY from a ``UUID`` object (canonical form): no client text ever
  becomes a path, so traversal ("../") and absolute-path injection are impossible.
- Reads open with ``O_NOFOLLOW`` and require a regular file: a symlink planted at a
  secret path is refused (never followed out of the root).
- ``replace`` writes a new 0600 temp file (``O_EXCL | O_NOFOLLOW``), fsyncs it and
  atomically renames it over the old file: the previous secret set stays intact until
  the new one is completely stored. Renaming over a planted symlink replaces the link
  itself, never its target.
- ``delete`` unlinks the file (a missing file is fine: deterministic and idempotent).
- Every failure is a ``SecretStoreError`` with a fixed message (no value, path or cause).

There is NO encryption here: confidentiality relies on the deployment's filesystem and
volume protection (an operator-owned, private volume). No KMS/HSM integration exists.
"""

import asyncio
import errno
import json
import os
import secrets as _random
import stat
from collections.abc import Mapping
from pathlib import Path
from uuid import UUID

from pydantic import SecretStr

from app.integration_management.secrets import SecretMaterialMissingError, SecretStoreError

FORMAT = 1
MAX_FILE_BYTES = 64 * 1024
_SUFFIX = ".json"


class FilesystemIntegrationSecretStore:
    def __init__(self, root: Path | str) -> None:
        path = Path(root)
        if not path.is_absolute():
            raise SecretStoreError("integration secret storage root must be an absolute path")
        self._root = path
        self._check_root()

    # ----- path safety ----------------------------------------------------------------------

    def _check_root(self) -> None:
        try:
            info = os.lstat(self._root)
        except OSError:
            raise SecretStoreError() from None
        if not stat.S_ISDIR(info.st_mode):  # lstat: a symlinked root is refused too
            raise SecretStoreError()
        if info.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
            raise SecretStoreError()

    def _path(self, connection_id: UUID) -> Path:
        if not isinstance(connection_id, UUID):
            raise SecretStoreError("invalid connection identifier")
        name = f"{UUID(str(connection_id))}{_SUFFIX}"  # canonical form only
        path = self._root / name
        if path.parent != self._root or path.name != name:
            raise SecretStoreError("invalid connection identifier")
        return path

    # ----- synchronous primitives (run in a worker thread) ------------------------------------

    def _read_values(self, connection_id: UUID) -> dict[str, str] | None:
        self._check_root()
        path = self._path(connection_id)
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        except FileNotFoundError:
            return None
        except OSError:
            raise SecretStoreError() from None  # ELOOP (symlink), permissions, ...
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_FILE_BYTES:
                raise SecretStoreError()
            data = os.read(fd, MAX_FILE_BYTES + 1)
        except OSError:
            raise SecretStoreError() from None
        finally:
            os.close(fd)
        try:
            document = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            raise SecretStoreError() from None
        values = document.get("values") if isinstance(document, dict) else None
        if (
            document.get("format") != FORMAT
            or not isinstance(values, dict)
            or not all(isinstance(k, str) and isinstance(v, str) for k, v in values.items())
        ):
            raise SecretStoreError()
        return values

    def _replace(self, connection_id: UUID, values: Mapping[str, SecretStr]) -> None:
        self._check_root()
        path = self._path(connection_id)
        if not all(isinstance(k, str) and isinstance(v, SecretStr) for k, v in values.items()):
            raise SecretStoreError()
        payload = json.dumps(
            {"format": FORMAT,
             "values": {k: v.get_secret_value() for k, v in sorted(values.items())}},
            separators=(",", ":"),
        ).encode("utf-8")  # fmt: skip
        if len(payload) > MAX_FILE_BYTES:
            raise SecretStoreError()
        temp = self._root / f".{path.stem}.{_random.token_hex(8)}.tmp"
        try:
            fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
                         | os.O_CLOEXEC, 0o600)  # fmt: skip
        except OSError:
            raise SecretStoreError() from None
        try:
            try:
                os.fchmod(fd, 0o600)
                view = memoryview(payload)
                while view:
                    view = view[os.write(fd, view) :]
                os.fsync(fd)
            finally:
                os.close(fd)
            os.replace(temp, path)  # atomic; replaces a planted symlink, never its target
        except OSError:
            try:
                os.unlink(temp)
            except OSError:
                pass
            raise SecretStoreError() from None
        self._sync_root()

    def _delete(self, connection_id: UUID) -> None:
        self._check_root()
        path = self._path(connection_id)
        try:
            os.unlink(path)  # a planted symlink is removed itself, never followed
        except FileNotFoundError:
            return
        except OSError:
            raise SecretStoreError() from None
        self._sync_root()

    def _sync_root(self) -> None:
        try:
            fd = os.open(self._root, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
        except OSError:
            return
        try:
            os.fsync(fd)
        except OSError as error:
            if error.errno not in (errno.EINVAL, errno.EBADF):
                raise SecretStoreError() from None
        finally:
            os.close(fd)

    # ----- IntegrationSecretStore --------------------------------------------------------------

    async def replace(self, connection_id: UUID, values: Mapping[str, SecretStr]) -> None:
        await asyncio.to_thread(self._replace, connection_id, dict(values))

    async def field_names(self, connection_id: UUID) -> frozenset[str]:
        values = await asyncio.to_thread(self._read_values, connection_id)
        return frozenset(values or ())

    async def read(self, connection_id: UUID, names: frozenset[str]) -> dict[str, SecretStr]:
        values = await asyncio.to_thread(self._read_values, connection_id)
        if values is None or not names <= set(values):
            raise SecretMaterialMissingError()
        return {name: SecretStr(values[name]) for name in sorted(names)}

    async def delete(self, connection_id: UUID) -> None:
        await asyncio.to_thread(self._delete, connection_id)
