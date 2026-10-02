"""Per-installation Agent configuration (deliberately narrow: enabled/disabled only).

A stored ``AgentConfiguration`` is an OVERRIDE of the definition default. It holds no
prompt, instruction, model, provider, credential, tool selection or code reference, so
stored values can never select a Python class or change an Agent's trusted behaviour.
"""

from datetime import datetime
from typing import Annotated, Protocol

from pydantic import AwareDatetime, BaseModel, ConfigDict, StrictBool, StringConstraints

from app.agent_management.definitions import AgentId

CompanyId = Annotated[str, StringConstraints(strict=True, min_length=1, max_length=200)]


class AgentConfiguration(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    company_id: CompanyId
    agent_id: AgentId
    enabled: StrictBool
    created_at: AwareDatetime
    updated_at: AwareDatetime


class AgentConfigurationRepositoryError(Exception):
    """Storage failed or returned unusable data. Fixed message, no details."""

    def __init__(self) -> None:
        super().__init__("agent configuration storage failed")


class AgentConfigurationRepository(Protocol):
    """Every call is scoped by the trusted company id."""

    async def get(self, company_id: str, agent_id: str) -> AgentConfiguration | None: ...

    async def list(self, company_id: str) -> tuple[AgentConfiguration, ...]: ...

    async def set_enabled(
        self, company_id: str, agent_id: str, enabled: bool, at: datetime
    ) -> AgentConfiguration: ...

    async def delete(self, company_id: str, agent_id: str) -> bool: ...
