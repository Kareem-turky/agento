"""Effective Agent state: definition default + stored override + runtime availability.

    override stored?  yes -> configured = override.enabled   (source "override")
                      no  -> configured = definition.default  (source "default")
    configured disabled                    -> availability "disabled"
    enabled, runtime not composed          -> availability "unavailable"
    enabled and runtime composed           -> availability "available"

"enabled" alone never implies runtime readiness. Reasons are stable Product codes: no
setting, model, provider or credential detail is ever a reason.
"""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from app.agent_management.configuration import AgentConfiguration
from app.agent_management.definitions import AgentDefinition


class ConfigurationSource(StrEnum):
    DEFAULT = "default"
    OVERRIDE = "override"


class AgentAvailability(StrEnum):
    AVAILABLE = "available"
    DISABLED = "disabled"
    UNAVAILABLE = "unavailable"


class AgentAvailabilityReason(StrEnum):
    DISABLED_BY_CONFIGURATION = "disabled_by_configuration"
    RUNTIME_NOT_COMPOSED = "runtime_not_composed"


class AgentEffectiveState(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    agent_id: str
    enabled: bool
    source: ConfigurationSource
    availability: AgentAvailability
    reason: AgentAvailabilityReason | None
    configuration: AgentConfiguration | None

    @property
    def runnable(self) -> bool:
        return self.availability is AgentAvailability.AVAILABLE


def effective_state(
    definition: AgentDefinition,
    override: AgentConfiguration | None,
    *,
    runtime_available: bool,
) -> AgentEffectiveState:
    if override is not None and override.agent_id != definition.agent_id:
        raise ValueError("configuration belongs to another agent")
    enabled = definition.default_enabled if override is None else override.enabled
    reason: AgentAvailabilityReason | None = None
    if not enabled:
        availability = AgentAvailability.DISABLED
        reason = AgentAvailabilityReason.DISABLED_BY_CONFIGURATION
    elif not runtime_available:
        availability = AgentAvailability.UNAVAILABLE
        reason = AgentAvailabilityReason.RUNTIME_NOT_COMPOSED
    else:
        availability = AgentAvailability.AVAILABLE
    return AgentEffectiveState(
        agent_id=definition.agent_id,
        enabled=enabled,
        source=ConfigurationSource.DEFAULT if override is None else ConfigurationSource.OVERRIDE,
        availability=availability,
        reason=reason,
        configuration=override,
    )
