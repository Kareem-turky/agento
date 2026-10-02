"""Provider-agnostic integration MANAGEMENT (Task 031): which integration types this build
installs (catalog of definitions + connection drivers), how this installation connects
to them (connection metadata), where their credentials live (the secret boundary), and
the governed, audited service that manages connections.

It never performs business operations: those belong to the domain contracts
(``app.integrations.commerce.CommerceIntegration`` and, later, messaging/marketing/...
contracts) implemented by provider adapters. No real provider is installed in this
build, and no business data is mirrored into Product storage.

Layering: ``definitions``, ``connections``, ``drivers``, ``catalog`` and ``secrets`` are
the domain (standard library and Pydantic only). ``filesystem_secrets`` is the
filesystem secret-store implementation. ``actions``, ``handlers`` and ``service`` are
the governed application layer (governance + execution + audit).
"""

from app.integration_management.catalog import (
    InstalledIntegration,
    IntegrationCatalog,
    build_default_integration_catalog,
)
from app.integration_management.connections import (
    ConnectionErrorCode,
    ConnectionRepositoryError,
    ConnectionTestResult,
    IntegrationConnection,
    IntegrationConnectionRepository,
)
from app.integration_management.definitions import (
    ConfigField,
    ConfigFieldKind,
    ConnectionConfigError,
    IntegrationAuthMode,
    IntegrationCategory,
    IntegrationDefinition,
)
from app.integration_management.drivers import ConnectionTestOutcome, IntegrationConnectionDriver
from app.integration_management.secrets import (
    IntegrationSecretStore,
    SecretMaterialMissingError,
    SecretStoreError,
)

__all__ = [
    "ConfigField",
    "ConfigFieldKind",
    "ConnectionConfigError",
    "ConnectionErrorCode",
    "ConnectionRepositoryError",
    "ConnectionTestOutcome",
    "ConnectionTestResult",
    "InstalledIntegration",
    "IntegrationAuthMode",
    "IntegrationCatalog",
    "IntegrationCategory",
    "IntegrationConnection",
    "IntegrationConnectionDriver",
    "IntegrationConnectionRepository",
    "IntegrationDefinition",
    "IntegrationSecretStore",
    "SecretMaterialMissingError",
    "SecretStoreError",
    "build_default_integration_catalog",
]
