"""Integration-management composition (Task 031), independent of the business backend.

    settings -> (database configured?)  no  -> no service (integration routes answer 503)
             -> engine + sessions -> PostgresIntegrationConnectionRepository
             -> APP_INTEGRATION_SECRETS_DIR set? -> FilesystemIntegrationSecretStore
                                         unset  -> no secret storage (credential-bearing
                                                   connections are refused, fail closed)
             -> catalog (this build: build_default_integration_catalog(), i.e. EMPTY)
             -> GovernanceGate(INTEGRATION_ACTIONS) + handlers + ExecutionCoordinator
                (+ PostgresAuditSink: the existing audit trail)
             -> IntegrationManagementService

``catalog`` is a seam for tests only (deterministic fake definitions/drivers); the
operator-facing factory never exposes it. Nothing here migrates or creates tables, and
nothing connects to any external system.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.composition.contracts import DeploymentCompositionError
from app.config import Settings

if TYPE_CHECKING:
    from app.integration_management import IntegrationCatalog
    from app.integration_management.service import IntegrationManagementService

SECRET_STORAGE_MISCONFIGURED = "integration secret storage is misconfigured"  # noqa: S105
RELEASE_FAILED = "integration management resources could not be released"


async def _nothing() -> None:
    return None


@dataclass(frozen=True)
class IntegrationManagementComposition:
    service: "IntegrationManagementService | None" = None
    close: Callable[[], Awaitable[None]] = _nothing
    discard: Callable[[], None] = lambda: None


def build_integration_management(
    settings: Settings, *, catalog: "IntegrationCatalog | None" = None
) -> IntegrationManagementComposition:
    if settings.database_url is None:
        return IntegrationManagementComposition()
    # Imported only here (called after the business backend was allowed): building a
    # refused deployment never loads persistence or integration management.
    from app.execution import ActionHandlerRegistry, ExecutionCoordinator
    from app.governance import ActionCatalog, GovernanceGate
    from app.integration_management import build_default_integration_catalog
    from app.integration_management.actions import INTEGRATION_ACTIONS
    from app.integration_management.filesystem_secrets import FilesystemIntegrationSecretStore
    from app.integration_management.handlers import build_integration_handlers
    from app.integration_management.secrets import IntegrationSecretStore, SecretStoreError
    from app.integration_management.service import IntegrationManagementService
    from app.persistence import (
        PostgresAuditSink,
        PostgresIntegrationConnectionRepository,
        create_product_engine,
        create_session_factory,
    )

    installed = catalog if catalog is not None else build_default_integration_catalog()
    secret_store: IntegrationSecretStore | None = None
    if settings.integration_secrets_dir is not None:
        try:
            secret_store = FilesystemIntegrationSecretStore(settings.integration_secrets_dir)
        except SecretStoreError:
            raise DeploymentCompositionError(SECRET_STORAGE_MISCONFIGURED) from None

    engine = create_product_engine(str(settings.database_url))
    released = False

    async def close() -> None:
        nonlocal released
        if released:
            return
        released = True
        failed = False
        for driver in installed.drivers():
            try:
                await driver.aclose()
            except Exception:  # noqa: BLE001 - a driver must not block the release
                failed = True
        try:
            await engine.dispose()
        except Exception:  # noqa: BLE001 - never surface driver/URL details
            failed = True
        if failed:
            raise DeploymentCompositionError(RELEASE_FAILED) from None

    def discard() -> None:
        nonlocal released
        if released:
            return
        released = True
        try:
            engine.sync_engine.dispose()
        except Exception:  # noqa: BLE001 - never surface driver/URL details
            raise DeploymentCompositionError(RELEASE_FAILED) from None

    try:
        sessions = create_session_factory(engine)
        repository = PostgresIntegrationConnectionRepository(sessions)
        gate = GovernanceGate(ActionCatalog(INTEGRATION_ACTIONS))
        handlers = ActionHandlerRegistry(
            build_integration_handlers(installed, repository, secret_store)
        )
        coordinator = ExecutionCoordinator(gate, handlers, PostgresAuditSink(sessions))
        service = IntegrationManagementService(installed, repository, secret_store, gate,
                                               coordinator)  # fmt: skip
        return IntegrationManagementComposition(service=service, close=close, discard=discard)
    except BaseException:
        discard()
        raise
