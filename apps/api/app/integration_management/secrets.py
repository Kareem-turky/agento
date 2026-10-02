"""The integration secret boundary.

Provider credentials are stored SEPARATELY from connection metadata, keyed by the
connection UUID, through ``IntegrationSecretStore``:

    replace(connection_id, values)   atomically REPLACE the whole secret set (the old set
                                     stays intact until the new one is completely stored)
    field_names(connection_id)       which secret fields are stored (names only)
    read(connection_id, names)       values, for trusted driver code ONLY
    delete(connection_id)            remove the connection's secret material (idempotent)

No HTTP or API caller can retrieve a stored value: no route, response model or service
method returns one. Errors carry fixed messages only (never a value, path or cause).
"""

from collections.abc import Mapping
from typing import Protocol, runtime_checkable
from uuid import UUID

from pydantic import SecretStr


class SecretStoreError(Exception):
    """Base class: the secret store failed. Fixed message, never a value or path."""

    def __init__(self, message: str = "integration secret storage unavailable") -> None:
        super().__init__(message)


class SecretMaterialMissingError(SecretStoreError):
    def __init__(self) -> None:
        super().__init__("integration secret material is missing")


@runtime_checkable
class IntegrationSecretStore(Protocol):
    async def replace(self, connection_id: UUID, values: Mapping[str, SecretStr]) -> None: ...

    async def field_names(self, connection_id: UUID) -> frozenset[str]: ...

    async def read(self, connection_id: UUID, names: frozenset[str]) -> dict[str, SecretStr]: ...

    async def delete(self, connection_id: UUID) -> None: ...
