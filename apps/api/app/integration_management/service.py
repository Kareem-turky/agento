"""``IntegrationManagementService``: the Product's provider-independent connection
management, used by the integration routes.

    reads      trusted RequestContext -> GovernanceGate (integrations.read)  -> 403 / data
    mutations  trusted RequestContext -> GovernanceGate (integrations.manage)
                 denied  -> ExecutionCoordinator (audits the denial)      -> 403
                 allowed -> precise prechecks (404 / 409 / 422 / 503, nothing changed)
                         -> ExecutionCoordinator -> handler -> verify -> AUDIT

Identity and company come only from the trusted ``RequestContext``. Results are
metadata only: no method returns a secret value. Every failure is one of the narrow
errors below with a fixed, value-free message.
"""

from collections.abc import Mapping
from typing import Any
from uuid import UUID

from pydantic import SecretStr

from app.context.models import ActorContext, RequestContext
from app.execution import ActionRunStatus, ExecutionCoordinator
from app.governance import (
    ActionDefinition,
    ActionIntent,
    ActionScope,
    GovernanceGate,
    PolicyOutcome,
)
from app.integration_management import actions
from app.integration_management.catalog import InstalledIntegration, IntegrationCatalog
from app.integration_management.connections import (
    ConnectionRepositoryError,
    IntegrationConnection,
    IntegrationConnectionRepository,
)
from app.integration_management.definitions import ConnectionConfigError, IntegrationDefinition
from app.integration_management.secrets import IntegrationSecretStore


class IntegrationManagementError(Exception):
    """Base class. Messages are fixed and never contain a submitted or stored value."""

    message = "integration management failed"

    def __init__(self) -> None:
        super().__init__(self.message)


class IntegrationAccessDeniedError(IntegrationManagementError):
    message = "Forbidden"


class IntegrationNotInstalledError(IntegrationManagementError):
    message = "Integration is not installed"


class IntegrationNotConnectableError(IntegrationManagementError):
    message = "This build cannot connect this integration"


class IntegrationConnectionNotFoundError(IntegrationManagementError):
    message = "Integration connection not found"


class InvalidIntegrationConfigError(IntegrationManagementError):
    message = "Invalid integration configuration"

    def __init__(self, code: str, field: str | None) -> None:
        super().__init__()
        self.code = code
        self.field = field  # a trusted, definition-declared field name or None


class IntegrationSecretStorageUnavailableError(IntegrationManagementError):
    message = "Integration secret storage unavailable"


class IntegrationOperationFailedError(IntegrationManagementError):
    message = "Integration operation did not complete"


class IntegrationServiceUnavailableError(IntegrationManagementError):
    message = "Integration management unavailable"


def _invalid(error: ConnectionConfigError) -> InvalidIntegrationConfigError:
    return InvalidIntegrationConfigError(error.code, error.field)


class IntegrationManagementService:
    def __init__(
        self,
        catalog: IntegrationCatalog,
        repository: IntegrationConnectionRepository,
        secrets: IntegrationSecretStore | None,
        gate: GovernanceGate,
        coordinator: ExecutionCoordinator,
    ) -> None:
        self._catalog = catalog
        self._repository = repository
        self._secrets = secrets
        self._gate = gate
        self._coordinator = coordinator

    # ----- authorization --------------------------------------------------------------------

    @staticmethod
    def _actor(context: RequestContext) -> ActorContext:
        if context.actor is None:
            raise IntegrationAccessDeniedError()
        return context.actor

    def _allowed(self, actor: ActorContext, action: ActionDefinition) -> bool:
        decision = self._gate.decide(
            actor, ActionIntent(name=action.name), ActionScope(company_id=actor.company_id)
        )
        return decision.outcome is PolicyOutcome.ALLOW

    def _authorize_read(self, context: RequestContext, action: ActionDefinition) -> ActorContext:
        actor = self._actor(context)
        if not self._allowed(actor, action):
            raise IntegrationAccessDeniedError()
        return actor

    async def _authorize_mutation(
        self, context: RequestContext, action: ActionDefinition
    ) -> ActorContext:
        actor = self._actor(context)
        if not self._allowed(actor, action):
            # The coordinator records the denial in the audit trail, then stops.
            await self._coordinator.run(
                context,
                ActionIntent(name=action.name),
                ActionScope(company_id=actor.company_id),
                {},
            )
            raise IntegrationAccessDeniedError()
        return actor

    async def _run(
        self,
        context: RequestContext,
        actor: ActorContext,
        action: ActionDefinition,
        parameters: Mapping[str, Any],
    ) -> str | None:
        run = await self._coordinator.run(
            context,
            ActionIntent(name=action.name),
            ActionScope(company_id=actor.company_id),
            parameters,
        )
        if run.status is ActionRunStatus.DENIED:
            raise IntegrationAccessDeniedError()
        if run.status is not ActionRunStatus.VERIFIED:
            raise IntegrationOperationFailedError()
        return run.execution_result.reference_id if run.execution_result else None

    # ----- lookups ----------------------------------------------------------------------------

    async def _connection(self, actor: ActorContext, connection_id: UUID) -> IntegrationConnection:
        try:
            connection = await self._repository.get(actor.company_id, connection_id)
        except ConnectionRepositoryError:
            raise IntegrationServiceUnavailableError() from None
        if connection is None:
            raise IntegrationConnectionNotFoundError()
        return connection

    def _installed(self, integration_id: str) -> InstalledIntegration:
        installed = self._catalog.get(integration_id)
        if installed is None:
            raise IntegrationNotInstalledError()
        if not installed.definition.connectable:
            raise IntegrationNotConnectableError()
        return installed

    # ----- reads ------------------------------------------------------------------------------

    def catalog(self, context: RequestContext) -> tuple[IntegrationDefinition, ...]:
        self._authorize_read(context, actions.CATALOG_READ)
        return self._catalog.definitions()

    async def list_connections(self, context: RequestContext) -> tuple[IntegrationConnection, ...]:
        actor = self._authorize_read(context, actions.CONNECTIONS_READ)
        try:
            return await self._repository.list(actor.company_id)
        except ConnectionRepositoryError:
            raise IntegrationServiceUnavailableError() from None

    async def get_connection(
        self, context: RequestContext, connection_id: UUID
    ) -> IntegrationConnection:
        actor = self._authorize_read(context, actions.CONNECTIONS_READ)
        return await self._connection(actor, connection_id)

    # ----- mutations ----------------------------------------------------------------------------

    async def create_connection(
        self,
        context: RequestContext,
        integration_id: str,
        display_name: str,
        config: Mapping[str, object],
        credentials: Mapping[str, SecretStr],
    ) -> IntegrationConnection:
        actor = await self._authorize_mutation(context, actions.CONNECTION_CREATE)
        installed = self._installed(integration_id)
        try:
            checked = installed.definition.validate_config(config)
            installed.driver.validate_config(checked)
            secrets = installed.definition.validate_secrets(credentials)
        except ConnectionConfigError as error:
            raise _invalid(error) from None
        if secrets and self._secrets is None:
            raise IntegrationSecretStorageUnavailableError()
        reference = await self._run(
            context,
            actor,
            actions.CONNECTION_CREATE,
            {
                "integration_id": integration_id,
                "display_name": display_name,
                "config": dict(config),
                "secrets": {k: v.get_secret_value() for k, v in secrets.items()},
            },
        )
        if reference is None:
            raise IntegrationOperationFailedError()
        return await self._connection(actor, UUID(reference))

    async def update_connection(
        self,
        context: RequestContext,
        connection_id: UUID,
        display_name: str | None,
        config: Mapping[str, object] | None,
    ) -> IntegrationConnection:
        actor = await self._authorize_mutation(context, actions.CONNECTION_UPDATE)
        connection = await self._connection(actor, connection_id)
        if config is not None:
            installed = self._installed(connection.integration_id)
            try:
                installed.driver.validate_config(installed.definition.validate_config(config))
            except ConnectionConfigError as error:
                raise _invalid(error) from None
        parameters: dict[str, Any] = {"connection_id": str(connection_id)}
        if display_name is not None:
            parameters["display_name"] = display_name
        if config is not None:
            parameters["config"] = dict(config)
        await self._run(context, actor, actions.CONNECTION_UPDATE, parameters)
        return await self._connection(actor, connection_id)

    async def replace_credentials(
        self, context: RequestContext, connection_id: UUID, credentials: Mapping[str, SecretStr]
    ) -> IntegrationConnection:
        actor = await self._authorize_mutation(context, actions.CREDENTIALS_REPLACE)
        connection = await self._connection(actor, connection_id)
        installed = self._installed(connection.integration_id)
        if not installed.definition.secret_field_names:
            raise InvalidIntegrationConfigError("no_credentials", None)
        try:
            secrets = installed.definition.validate_secrets(credentials)
        except ConnectionConfigError as error:
            raise _invalid(error) from None
        if self._secrets is None:
            raise IntegrationSecretStorageUnavailableError()
        await self._run(
            context,
            actor,
            actions.CREDENTIALS_REPLACE,
            {
                "connection_id": str(connection_id),
                "secrets": {k: v.get_secret_value() for k, v in secrets.items()},
            },
        )
        return await self._connection(actor, connection_id)

    async def test_connection(
        self, context: RequestContext, connection_id: UUID
    ) -> IntegrationConnection:
        actor = await self._authorize_mutation(context, actions.CONNECTION_TEST)
        connection = await self._connection(actor, connection_id)
        self._installed(connection.integration_id)
        await self._run(
            context, actor, actions.CONNECTION_TEST, {"connection_id": str(connection_id)}
        )
        return await self._connection(actor, connection_id)

    async def set_enabled(
        self, context: RequestContext, connection_id: UUID, enabled: bool
    ) -> IntegrationConnection:
        action = actions.CONNECTION_ENABLE if enabled else actions.CONNECTION_DISABLE
        actor = await self._authorize_mutation(context, action)
        await self._connection(actor, connection_id)
        await self._run(
            context, actor, action, {"connection_id": str(connection_id), "enabled": enabled}
        )
        return await self._connection(actor, connection_id)

    async def delete_connection(self, context: RequestContext, connection_id: UUID) -> None:
        actor = await self._authorize_mutation(context, actions.CONNECTION_DELETE)
        connection = await self._connection(actor, connection_id)
        if connection.secret_fields and self._secrets is None:
            raise IntegrationSecretStorageUnavailableError()
        await self._run(
            context, actor, actions.CONNECTION_DELETE, {"connection_id": str(connection_id)}
        )
