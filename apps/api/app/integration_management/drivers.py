"""The connection-lifecycle driver contract: connection MANAGEMENT only.

    IntegrationConnectionDriver
      validate_config(config)            provider-specific checks of the non-secret config
      test_connection(config, secrets)    one authentication/connectivity check
      aclose()                            release resources, if any

It deliberately has NO business operations (no orders, inventory, messages, refunds or
campaign budgets): those belong to the domain contracts (``CommerceIntegration`` and
later messaging/marketing/... contracts), which a provider adapter implements
separately. A future provider ships one reviewed driver per integration definition.

Drivers receive secrets only from trusted Product code, must never log or return them,
and report failures only as a provider-independent ``ConnectionErrorCode``.
"""

from collections.abc import Mapping
from typing import Protocol, Self, runtime_checkable

from pydantic import BaseModel, ConfigDict, SecretStr, StrictBool, model_validator

from app.integration_management.connections import ConnectionErrorCode
from app.integration_management.definitions import ConfigValue


class ConnectionTestOutcome(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    succeeded: StrictBool
    error: ConnectionErrorCode | None = None

    @model_validator(mode="after")
    def _error_iff_failed(self) -> Self:
        if self.succeeded == (self.error is not None):
            raise ValueError("a failed test carries an error code, a successful one none")
        return self


@runtime_checkable
class IntegrationConnectionDriver(Protocol):
    @property
    def integration_id(self) -> str: ...

    def validate_config(self, config: Mapping[str, ConfigValue]) -> None:
        """Raise ``ConnectionConfigError`` for provider-specific invalid configuration."""
        ...

    async def test_connection(
        self, config: Mapping[str, ConfigValue], secrets: Mapping[str, SecretStr]
    ) -> ConnectionTestOutcome: ...

    async def aclose(self) -> None: ...
