"""Integration connections: Product-owned METADATA about how this installation connects
to an installed integration. Never business data and never a secret value.

Two independent states:
- configuration: ``enabled`` (the operator's choice);
- last-known connectivity: ``last_test_result`` (never_tested / success / failure) at
  ``last_tested_at``, with a provider-independent ``last_test_error``. A success is the
  result of ONE test at that instant, never a promise that the provider is reachable now.

``secret_fields`` holds the NAMES of the configured secret fields; the values live only
in the ``IntegrationSecretStore``.
"""

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Protocol, Self, runtime_checkable
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    StrictBool,
    StrictStr,
    StringConstraints,
    model_validator,
)

from app.integration_management.definitions import FieldName, IntegrationId, looks_secret

DisplayName = Annotated[
    str, StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=120)
]
CompanyId = Annotated[str, StringConstraints(strict=True, min_length=1, max_length=256)]


class ConnectionTestResult(StrEnum):
    NEVER_TESTED = "never_tested"
    SUCCESS = "success"
    FAILURE = "failure"


class ConnectionErrorCode(StrEnum):
    """Provider-independent classification of a failed connection test. Drivers map
    their provider-specific failures here; nothing provider-specific is stored."""

    AUTHENTICATION_FAILED = "authentication_failed"
    PERMISSION_DENIED = "permission_denied"
    UNREACHABLE = "unreachable"
    TIMEOUT = "timeout"
    INVALID_CONFIGURATION = "invalid_configuration"
    CREDENTIALS_UNAVAILABLE = "credentials_unavailable"
    UNEXPECTED_RESPONSE = "unexpected_response"
    PROVIDER_ERROR = "provider_error"


class IntegrationConnection(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    connection_id: UUID
    company_id: CompanyId
    integration_id: IntegrationId
    display_name: DisplayName
    config: dict[FieldName, StrictStr | StrictBool]
    secret_fields: frozenset[FieldName]
    enabled: StrictBool
    created_at: AwareDatetime
    updated_at: AwareDatetime
    last_tested_at: AwareDatetime | None = None
    last_test_result: ConnectionTestResult = ConnectionTestResult.NEVER_TESTED
    last_test_error: ConnectionErrorCode | None = None

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        # Defense in depth: no credential-like key may ever sit in the plain metadata.
        if any(looks_secret(key) for key in self.config):
            raise ValueError("connection config must not contain credential-like keys")
        if set(self.config) & self.secret_fields:
            raise ValueError("a field is either configuration or secret, never both")
        never = self.last_test_result is ConnectionTestResult.NEVER_TESTED
        if never != (self.last_tested_at is None):
            raise ValueError("last_tested_at is set if and only if a test was recorded")
        failed = self.last_test_result is ConnectionTestResult.FAILURE
        if failed != (self.last_test_error is not None):
            raise ValueError("an error code is recorded if and only if the test failed")
        return self

    def untested(self, at: datetime) -> "IntegrationConnection":
        """The same connection after a configuration/credential change: the previous
        test no longer describes it."""
        return self.model_copy(update={
            "updated_at": at, "last_tested_at": None,
            "last_test_result": ConnectionTestResult.NEVER_TESTED, "last_test_error": None,
        })  # fmt: skip


class ConnectionRepositoryError(Exception):
    """The connection metadata store could not answer or stored invalid data. Fixed
    message only: no SQL, URL, driver detail or stored value."""

    def __init__(self) -> None:
        super().__init__("integration connection storage unavailable")


@runtime_checkable
class IntegrationConnectionRepository(Protocol):
    """Product-owned persistence of connection METADATA. Every call is scoped by the
    trusted company id; another company's connection is indistinguishable from none."""

    async def insert(self, connection: IntegrationConnection) -> None: ...

    async def get(self, company_id: str, connection_id: UUID) -> IntegrationConnection | None: ...

    async def list(self, company_id: str) -> tuple[IntegrationConnection, ...]: ...

    async def update(self, connection: IntegrationConnection) -> bool:
        """Replace the stored state of an existing connection; False if it is gone."""
        ...

    async def delete(self, company_id: str, connection_id: UUID) -> bool: ...
