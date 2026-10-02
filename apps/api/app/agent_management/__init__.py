"""Product Agent management (Task 032): immutable Product Agent definitions and
manifests, the explicit ``ProductAgentCatalog``, per-installation enable/disable
configuration and the effective Agent state. Domain modules depend only on the standard
library and Pydantic; nothing here builds, imports or runs an Agent.
"""

from app.agent_management.catalog import (
    OPERATIONS_AGENT_DEFINITION,
    ProductAgentCatalog,
    build_default_agent_catalog,
)
from app.agent_management.configuration import (
    AgentConfiguration,
    AgentConfigurationRepository,
    AgentConfigurationRepositoryError,
)
from app.agent_management.definitions import (
    AgentCategory,
    AgentDefinition,
    AgentLifecycle,
    AgentManifest,
    AgentSafetyProperty,
    AgentToolAccess,
    AgentToolDeclaration,
)
from app.agent_management.state import (
    AgentAvailability,
    AgentAvailabilityReason,
    AgentEffectiveState,
    ConfigurationSource,
    effective_state,
)

__all__ = [
    "OPERATIONS_AGENT_DEFINITION",
    "AgentAvailability",
    "AgentAvailabilityReason",
    "AgentCategory",
    "AgentConfiguration",
    "AgentConfigurationRepository",
    "AgentConfigurationRepositoryError",
    "AgentDefinition",
    "AgentEffectiveState",
    "AgentLifecycle",
    "AgentManifest",
    "AgentSafetyProperty",
    "AgentToolAccess",
    "AgentToolDeclaration",
    "ConfigurationSource",
    "ProductAgentCatalog",
    "build_default_agent_catalog",
    "effective_state",
]
