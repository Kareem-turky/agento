"""Governed handlers of the integration-management mutations.

Each mutation is an ``ActionHandler`` run by ``ExecutionCoordinator`` (governance ->
validate -> execute -> verify -> audit), so it is audited by the EXISTING audit
architecture. Audit events carry only the action name, actor, company, run status and a
verification code: never parameters, configuration or secret values.

Handlers take company and actor only from the trusted ``ActionExecutionContext``.
Secret values travel only from ``validate`` to the ``IntegrationSecretStore`` (and, for a
test, from the store to the driver); they are never persisted with the metadata, returned
or included in an error.
"""

import asyncio
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, SecretStr, StrictBool, ValidationError

from app.execution import (
    ActionExecutionContext,
    ExecutionFailedWithoutEffect,
    ExecutionResult,
    VerificationResult,
)
from app.execution.errors import ActionExecutionError, ActionInputError
from app.integration_management import actions
from app.integration_management.catalog import InstalledIntegration, IntegrationCatalog
from app.integration_management.connections import (
    ConnectionErrorCode,
    ConnectionRepositoryError,
    ConnectionTestResult,
    DisplayName,
    IntegrationConnection,
    IntegrationConnectionRepository,
)
from app.integration_management.definitions import (
    ConfigValue,
    ConnectionConfigError,
    IntegrationId,
)
from app.integration_management.drivers import ConnectionTestOutcome
from app.integration_management.secrets import (
    IntegrationSecretStore,
    SecretMaterialMissingError,
    SecretStoreError,
)

DEFAULT_TEST_TIMEOUT_SECONDS = 15.0
_FROZEN = ConfigDict(frozen=True, extra="forbid")


def _utc_now() -> datetime:
    return datetime.now(UTC)


class _Target(BaseModel):
    model_config = _FROZEN
    connection_id: UUID


class _CreateInput(BaseModel):
    model_config = _FROZEN
    integration_id: IntegrationId
    display_name: DisplayName
    config: dict[str, Any]
    secrets: dict[str, SecretStr]


class _UpdateInput(BaseModel):
    model_config = _FROZEN
    connection_id: UUID
    display_name: DisplayName | None = None
    config: dict[str, Any] | None = None


class _CredentialsInput(BaseModel):
    model_config = _FROZEN
    connection_id: UUID
    secrets: dict[str, SecretStr]


class _EnabledInput(BaseModel):
    model_config = _FROZEN
    connection_id: UUID
    enabled: StrictBool


def _parse[M: BaseModel](model: type[M], parameters: Mapping[str, Any]) -> M:
    try:
        return model.model_validate(dict(parameters))
    except ValidationError:
        # Never chained: a validation error could carry a submitted value.
        raise ActionInputError("invalid integration management input") from None


def _ok(code: str) -> VerificationResult:
    return VerificationResult(verified=True, reason_code=code)


def _failed(code: str) -> VerificationResult:
    return VerificationResult(verified=False, reason_code=code)


class _NoEffect(ExecutionFailedWithoutEffect):
    def __init__(self) -> None:
        super().__init__("integration management operation did not run")


class _Base:
    action_name: str = ""

    def __init__(
        self,
        catalog: IntegrationCatalog,
        repository: IntegrationConnectionRepository,
        secrets: IntegrationSecretStore | None,
        clock: Callable[[], datetime],
    ) -> None:
        self._catalog = catalog
        self._repository = repository
        self._secrets = secrets
        self._clock = clock

    async def _load(
        self, context: ActionExecutionContext, connection_id: UUID
    ) -> IntegrationConnection:
        try:
            connection = await self._repository.get(context.company_id, connection_id)
        except ConnectionRepositoryError:
            raise _NoEffect() from None
        if connection is None:
            raise _NoEffect()
        return connection

    async def _reread(
        self, context: ActionExecutionContext, connection_id: UUID
    ) -> IntegrationConnection | None:
        try:
            return await self._repository.get(context.company_id, connection_id)
        except ConnectionRepositoryError:
            return None

    async def _update(self, connection: IntegrationConnection) -> None:
        try:
            updated = await self._repository.update(connection)
        except ConnectionRepositoryError:
            raise ActionExecutionError() from None  # the write may have committed
        if not updated:
            raise _NoEffect()

    def _installed(self, integration_id: str) -> InstalledIntegration:
        installed = self._catalog.get(integration_id)
        if installed is None or not installed.definition.connectable:
            raise _NoEffect()
        return installed


class CreateConnectionHandler(_Base):
    action_name = actions.CONNECTION_CREATE.name

    def __init__(self, *args: Any, id_factory: Callable[[], UUID] = uuid4, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._new_id = id_factory

    def validate(self, parameters: Mapping[str, Any]) -> BaseModel:
        data = _parse(_CreateInput, parameters)
        installed = self._catalog.get(data.integration_id)
        if installed is None or not installed.definition.connectable:
            raise ActionInputError("integration is not installed or not connectable")
        try:
            config = installed.definition.validate_config(data.config)
            installed.driver.validate_config(config)
            secrets = installed.definition.validate_secrets(data.secrets)
        except ConnectionConfigError:
            raise ActionInputError("invalid connection configuration") from None
        return data.model_copy(update={"config": config, "secrets": secrets})

    async def execute(
        self, context: ActionExecutionContext, validated_input: BaseModel
    ) -> ExecutionResult:
        data = cast(_CreateInput, validated_input)
        self._installed(data.integration_id)
        connection_id = self._new_id()
        now = self._clock()
        connection = IntegrationConnection(
            connection_id=connection_id,
            company_id=context.company_id,
            integration_id=data.integration_id,
            display_name=data.display_name,
            config=data.config,
            secret_fields=frozenset(data.secrets),
            enabled=True,
            created_at=now,
            updated_at=now,
        )
        if data.secrets:
            if self._secrets is None:
                raise _NoEffect()
            try:
                await self._secrets.replace(connection_id, data.secrets)
            except SecretStoreError:
                raise _NoEffect() from None
        try:
            await self._repository.insert(connection)
        except ConnectionRepositoryError:
            if data.secrets and self._secrets is not None:
                try:
                    await self._secrets.delete(connection_id)
                except SecretStoreError:
                    pass
            raise ActionExecutionError() from None
        return ExecutionResult(reference_id=str(connection_id))

    async def verify(
        self,
        context: ActionExecutionContext,
        validated_input: BaseModel,
        execution_result: ExecutionResult | None,
    ) -> VerificationResult:
        data = cast(_CreateInput, validated_input)
        if execution_result is None or execution_result.reference_id is None:
            return _failed("connection_not_confirmed")
        connection_id = UUID(execution_result.reference_id)
        stored = await self._reread(context, connection_id)
        if stored is None or stored.integration_id != data.integration_id:
            return _failed("connection_not_found")
        if stored.secret_fields:
            if self._secrets is None:
                return _failed("credentials_not_stored")
            try:
                names = await self._secrets.field_names(connection_id)
            except SecretStoreError:
                return _failed("credentials_not_stored")
            if names != stored.secret_fields:
                return _failed("credentials_not_stored")
        return _ok("connection_created")


class UpdateConnectionHandler(_Base):
    action_name = actions.CONNECTION_UPDATE.name

    def validate(self, parameters: Mapping[str, Any]) -> BaseModel:
        data = _parse(_UpdateInput, parameters)
        if data.display_name is None and data.config is None:
            raise ActionInputError("nothing to update")
        return data

    async def execute(
        self, context: ActionExecutionContext, validated_input: BaseModel
    ) -> ExecutionResult:
        data = cast(_UpdateInput, validated_input)
        connection = await self._load(context, data.connection_id)
        installed = self._installed(connection.integration_id)
        changes: dict[str, Any] = {}
        if data.display_name is not None:
            changes["display_name"] = data.display_name
        if data.config is not None:
            try:
                config = installed.definition.validate_config(data.config)
                installed.driver.validate_config(config)
            except ConnectionConfigError:
                raise _NoEffect() from None
            changes["config"] = config
        updated = (
            connection.untested(self._clock())
            if "config" in changes
            else connection.model_copy(update={"updated_at": self._clock()})
        )
        # Credentials are untouched: updating configuration never clears them.
        await self._update(updated.model_copy(update=changes))
        return ExecutionResult(reference_id=str(data.connection_id))

    async def verify(
        self,
        context: ActionExecutionContext,
        validated_input: BaseModel,
        execution_result: ExecutionResult | None,
    ) -> VerificationResult:
        data = cast(_UpdateInput, validated_input)
        stored = await self._reread(context, data.connection_id)
        if stored is None:
            return _failed("connection_not_found")
        if data.display_name is not None and stored.display_name != data.display_name:
            return _failed("connection_not_updated")
        return _ok("connection_updated")


class ReplaceCredentialsHandler(_Base):
    action_name = actions.CREDENTIALS_REPLACE.name

    def validate(self, parameters: Mapping[str, Any]) -> BaseModel:
        return _parse(_CredentialsInput, parameters)

    async def execute(
        self, context: ActionExecutionContext, validated_input: BaseModel
    ) -> ExecutionResult:
        data = cast(_CredentialsInput, validated_input)
        connection = await self._load(context, data.connection_id)
        installed = self._installed(connection.integration_id)
        try:
            secrets = installed.definition.validate_secrets(data.secrets)
        except ConnectionConfigError:
            raise _NoEffect() from None
        if not secrets or self._secrets is None:
            raise _NoEffect()
        try:
            # Atomic: the previous credentials stay intact unless this fully succeeds.
            await self._secrets.replace(data.connection_id, secrets)
        except SecretStoreError:
            raise _NoEffect() from None
        updated = connection.untested(self._clock()).model_copy(
            update={"secret_fields": frozenset(secrets)}
        )
        await self._update(updated)
        return ExecutionResult(reference_id=str(data.connection_id))

    async def verify(
        self,
        context: ActionExecutionContext,
        validated_input: BaseModel,
        execution_result: ExecutionResult | None,
    ) -> VerificationResult:
        data = cast(_CredentialsInput, validated_input)
        stored = await self._reread(context, data.connection_id)
        if stored is None or self._secrets is None:
            return _failed("connection_not_found")
        try:
            names = await self._secrets.field_names(data.connection_id)
        except SecretStoreError:
            return _failed("credentials_not_stored")
        return (
            _ok("credentials_replaced")
            if names == stored.secret_fields
            else _failed("credentials_not_stored")
        )


class TestConnectionHandler(_Base):
    action_name = actions.CONNECTION_TEST.name

    def __init__(
        self, *args: Any, timeout_seconds: float = DEFAULT_TEST_TIMEOUT_SECONDS, **kwargs: Any
    ) -> None:
        super().__init__(*args, **kwargs)
        self._timeout = timeout_seconds

    def validate(self, parameters: Mapping[str, Any]) -> BaseModel:
        return _parse(_Target, parameters)

    async def _outcome(self, connection: IntegrationConnection) -> ConnectionTestOutcome:
        def failed(code: ConnectionErrorCode) -> ConnectionTestOutcome:
            return ConnectionTestOutcome(succeeded=False, error=code)

        installed = self._installed(connection.integration_id)
        secrets: dict[str, SecretStr] = {}
        if connection.secret_fields:
            if self._secrets is None:
                return failed(ConnectionErrorCode.CREDENTIALS_UNAVAILABLE)
            try:
                secrets = await self._secrets.read(
                    connection.connection_id, connection.secret_fields
                )
            except (SecretMaterialMissingError, SecretStoreError):
                return failed(ConnectionErrorCode.CREDENTIALS_UNAVAILABLE)
        config: Mapping[str, ConfigValue] = dict(connection.config)
        try:
            outcome = await asyncio.wait_for(
                installed.driver.test_connection(config, secrets), self._timeout
            )
        except TimeoutError:
            return failed(ConnectionErrorCode.TIMEOUT)
        except Exception:  # noqa: BLE001 - a driver failure is a classified result, never a leak
            return failed(ConnectionErrorCode.PROVIDER_ERROR)
        if not isinstance(outcome, ConnectionTestOutcome):
            return failed(ConnectionErrorCode.UNEXPECTED_RESPONSE)
        return outcome

    async def execute(
        self, context: ActionExecutionContext, validated_input: BaseModel
    ) -> ExecutionResult:
        data = cast(_Target, validated_input)
        connection = await self._load(context, data.connection_id)
        outcome = await self._outcome(connection)
        at = self._clock()
        result = ConnectionTestResult.SUCCESS if outcome.succeeded else ConnectionTestResult.FAILURE
        await self._update(
            connection.model_copy(
                update={
                    "last_tested_at": at,
                    "last_test_result": result,
                    "last_test_error": outcome.error,
                }
            )
        )
        return ExecutionResult(reference_id=str(data.connection_id))

    async def verify(
        self,
        context: ActionExecutionContext,
        validated_input: BaseModel,
        execution_result: ExecutionResult | None,
    ) -> VerificationResult:
        data = cast(_Target, validated_input)
        stored = await self._reread(context, data.connection_id)
        if stored is None or stored.last_tested_at is None:
            return _failed("test_not_recorded")
        if stored.last_test_result is ConnectionTestResult.SUCCESS:
            return _ok("connection_test_succeeded")
        return _ok("connection_test_failed")  # the test ran and its failure is recorded


class SetEnabledHandler(_Base):
    def __init__(self, *args: Any, enabled: bool, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._enabled = enabled
        self.action_name = (
            actions.CONNECTION_ENABLE if enabled else actions.CONNECTION_DISABLE
        ).name

    def validate(self, parameters: Mapping[str, Any]) -> BaseModel:
        data = _parse(_EnabledInput, parameters)
        if data.enabled != self._enabled:
            raise ActionInputError("enabled flag does not match the action")
        return data

    async def execute(
        self, context: ActionExecutionContext, validated_input: BaseModel
    ) -> ExecutionResult:
        data = cast(_EnabledInput, validated_input)
        connection = await self._load(context, data.connection_id)
        await self._update(
            connection.model_copy(update={"enabled": self._enabled, "updated_at": self._clock()})
        )
        return ExecutionResult(reference_id=str(data.connection_id))

    async def verify(
        self,
        context: ActionExecutionContext,
        validated_input: BaseModel,
        execution_result: ExecutionResult | None,
    ) -> VerificationResult:
        data = cast(_EnabledInput, validated_input)
        stored = await self._reread(context, data.connection_id)
        if stored is None or stored.enabled != self._enabled:
            return _failed("connection_not_updated")
        return _ok("connection_enabled" if self._enabled else "connection_disabled")


class DeleteConnectionHandler(_Base):
    action_name = actions.CONNECTION_DELETE.name

    def validate(self, parameters: Mapping[str, Any]) -> BaseModel:
        return _parse(_Target, parameters)

    async def execute(
        self, context: ActionExecutionContext, validated_input: BaseModel
    ) -> ExecutionResult:
        data = cast(_Target, validated_input)
        connection = await self._load(context, data.connection_id)
        if self._secrets is not None:
            try:
                await self._secrets.delete(data.connection_id)
            except SecretStoreError:
                raise _NoEffect() from None
        elif connection.secret_fields:
            raise _NoEffect()  # secret material could not be removed: keep the metadata
        try:
            deleted = await self._repository.delete(context.company_id, data.connection_id)
        except ConnectionRepositoryError:
            raise ActionExecutionError() from None
        if not deleted:
            raise _NoEffect()
        return ExecutionResult(reference_id=str(data.connection_id))

    async def verify(
        self,
        context: ActionExecutionContext,
        validated_input: BaseModel,
        execution_result: ExecutionResult | None,
    ) -> VerificationResult:
        data = cast(_Target, validated_input)
        try:
            stored = await self._repository.get(context.company_id, data.connection_id)
        except ConnectionRepositoryError:
            return _failed("connection_state_unknown")
        if stored is not None:
            return _failed("connection_not_deleted")
        if self._secrets is not None:
            try:
                if await self._secrets.field_names(data.connection_id):
                    return _failed("credentials_not_deleted")
            except SecretStoreError:
                return _failed("credentials_state_unknown")
        return _ok("connection_deleted")


def build_integration_handlers(
    catalog: IntegrationCatalog,
    repository: IntegrationConnectionRepository,
    secrets: IntegrationSecretStore | None,
    *,
    clock: Callable[[], datetime] = _utc_now,
    test_timeout_seconds: float = DEFAULT_TEST_TIMEOUT_SECONDS,
) -> tuple[Any, ...]:
    common = (catalog, repository, secrets, clock)
    return (
        CreateConnectionHandler(*common),
        UpdateConnectionHandler(*common),
        ReplaceCredentialsHandler(*common),
        TestConnectionHandler(*common, timeout_seconds=test_timeout_seconds),
        SetEnabledHandler(*common, enabled=True),
        SetEnabledHandler(*common, enabled=False),
        DeleteConnectionHandler(*common),
    )
