"""Governed handlers of the Agent-management mutations.

Each mutation is an ``ActionHandler`` run by ``ExecutionCoordinator`` (governance ->
validate -> execute -> verify -> audit), so it is audited by the EXISTING audit trail:
the action name, actor, company, run status and a verification code. Company and actor
come only from the trusted ``ActionExecutionContext``. Only installed Agent ids are
accepted; nothing here builds, imports or runs an Agent.
"""

from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any, cast

from pydantic import BaseModel, ConfigDict, StrictBool, ValidationError

from app.agent_management import actions
from app.agent_management.catalog import ProductAgentCatalog
from app.agent_management.configuration import (
    AgentConfigurationRepository,
    AgentConfigurationRepositoryError,
)
from app.agent_management.definitions import AgentId
from app.execution import (
    ActionExecutionContext,
    ExecutionResult,
    VerificationResult,
)
from app.execution.errors import ActionExecutionError, ActionInputError

_FROZEN = ConfigDict(frozen=True, extra="forbid")


def _utc_now() -> datetime:
    return datetime.now(UTC)


class _Target(BaseModel):
    model_config = _FROZEN
    agent_id: AgentId


class _EnabledInput(BaseModel):
    model_config = _FROZEN
    agent_id: AgentId
    enabled: StrictBool


def _parse[M: BaseModel](model: type[M], parameters: Mapping[str, Any]) -> M:
    try:
        return model.model_validate(dict(parameters))
    except ValidationError:
        raise ActionInputError("invalid agent management input") from None


class _Base:
    action_name: str = ""

    def __init__(
        self,
        catalog: ProductAgentCatalog,
        repository: AgentConfigurationRepository,
        clock: Callable[[], datetime],
    ) -> None:
        self._catalog = catalog
        self._repository = repository
        self._clock = clock

    def _installed(self, agent_id: str) -> None:
        if agent_id not in self._catalog:
            raise ActionInputError("unknown agent")


class SetAgentEnabledHandler(_Base):
    def __init__(self, *args: Any, enabled: bool, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._enabled = enabled
        self.action_name = (actions.AGENT_ENABLE if enabled else actions.AGENT_DISABLE).name

    def validate(self, parameters: Mapping[str, Any]) -> BaseModel:
        data = _parse(_EnabledInput, parameters)
        if data.enabled != self._enabled:
            raise ActionInputError("enabled flag does not match the action")
        self._installed(data.agent_id)
        return data

    async def execute(
        self, context: ActionExecutionContext, validated_input: BaseModel
    ) -> ExecutionResult:
        data = cast(_EnabledInput, validated_input)
        try:
            await self._repository.set_enabled(
                context.company_id, data.agent_id, self._enabled, self._clock()
            )
        except AgentConfigurationRepositoryError:
            raise ActionExecutionError() from None  # the write may have committed
        return ExecutionResult(reference_id=data.agent_id)

    async def verify(
        self,
        context: ActionExecutionContext,
        validated_input: BaseModel,
        execution_result: ExecutionResult | None,
    ) -> VerificationResult:
        data = cast(_EnabledInput, validated_input)
        try:
            stored = await self._repository.get(context.company_id, data.agent_id)
        except AgentConfigurationRepositoryError:
            return VerificationResult(verified=False, reason_code="agent_state_unknown")
        if stored is None or stored.enabled != self._enabled:
            return VerificationResult(verified=False, reason_code="agent_not_updated")
        return VerificationResult(
            verified=True, reason_code="agent_enabled" if self._enabled else "agent_disabled"
        )


class ResetAgentConfigurationHandler(_Base):
    action_name = actions.CONFIGURATION_RESET.name

    def validate(self, parameters: Mapping[str, Any]) -> BaseModel:
        data = _parse(_Target, parameters)
        self._installed(data.agent_id)
        return data

    async def execute(
        self, context: ActionExecutionContext, validated_input: BaseModel
    ) -> ExecutionResult:
        data = cast(_Target, validated_input)
        try:
            await self._repository.delete(context.company_id, data.agent_id)
        except AgentConfigurationRepositoryError:
            raise ActionExecutionError() from None
        return ExecutionResult(reference_id=data.agent_id)

    async def verify(
        self,
        context: ActionExecutionContext,
        validated_input: BaseModel,
        execution_result: ExecutionResult | None,
    ) -> VerificationResult:
        data = cast(_Target, validated_input)
        try:
            stored = await self._repository.get(context.company_id, data.agent_id)
        except AgentConfigurationRepositoryError:
            return VerificationResult(verified=False, reason_code="agent_state_unknown")
        if stored is not None:
            return VerificationResult(verified=False, reason_code="configuration_not_reset")
        return VerificationResult(verified=True, reason_code="configuration_reset")


def build_agent_management_handlers(
    catalog: ProductAgentCatalog,
    repository: AgentConfigurationRepository,
    *,
    clock: Callable[[], datetime] = _utc_now,
) -> tuple[Any, ...]:
    common = (catalog, repository, clock)
    return (
        SetAgentEnabledHandler(*common, enabled=True),
        SetAgentEnabledHandler(*common, enabled=False),
        ResetAgentConfigurationHandler(*common),
    )
