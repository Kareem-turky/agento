"""The Product run boundary's Agent gate.

``AgentGatedOperationsRunService`` implements the Product ``OperationsRunService``
contract around the trusted Operations runner. Before every run it asks the Agent
management for the effective state of the ``operations`` Agent in the trusted run scope:

    disabled           -> OperationsAgentDisabledError (no Agent, model or tool runs)
    any other refusal  -> raised (fails closed; the route answers 503)
    available          -> delegate.run_product(...) unchanged

There is no fallback Agent: a refused run is never handed to anything else.
"""

from typing import Protocol

from app.agent_management.state import AgentAvailability, AgentEffectiveState
from app.context.models import RequestContext
from app.employee_chat.models import ChatRunResult, HistoryTurn
from app.governance import ActionScope
from app.services.operations import (
    OperationsAgentDisabledError,
    OperationsRunService,
    ProductOperationsRunResult,
)

OPERATIONS_AGENT_ID = "operations"


class AgentRuntimeStates(Protocol):
    async def runtime_state(self, company_id: str, agent_id: str) -> AgentEffectiveState: ...


class AgentNotRunnableError(Exception):
    def __init__(self) -> None:
        super().__init__("agent is not runnable")


class AgentGatedOperationsRunService:
    def __init__(self, delegate: OperationsRunService, states: AgentRuntimeStates) -> None:
        self.delegate = delegate
        self._states = states

    async def run_product(
        self, request: RequestContext, scope: ActionScope, message: str
    ) -> ProductOperationsRunResult:
        state = await self._states.runtime_state(scope.company_id, OPERATIONS_AGENT_ID)
        if state.availability is AgentAvailability.DISABLED:
            raise OperationsAgentDisabledError()
        if not state.runnable:
            raise AgentNotRunnableError()
        return await self.delegate.run_product(request, scope, message)


class OperationsChatRunner(Protocol):
    async def ensure_runnable(self, scope: ActionScope) -> None: ...

    async def run_chat(self, request: RequestContext, scope: ActionScope, message: str,
                       history: tuple[HistoryTurn, ...]) -> ChatRunResult: ...  # fmt: skip


class AgentGatedOperationsChatRunService:
    """Implements ``app.employee_chat.contracts.OperationsChatRunService`` (Task 042) with
    the SAME Agent gate: checked before anything is recorded (``ensure_runnable``) and
    again immediately before the run (``run_chat``)."""

    def __init__(self, delegate: OperationsChatRunner, states: AgentRuntimeStates) -> None:
        self.delegate = delegate
        self._states = states

    async def ensure_runnable(self, scope: ActionScope) -> None:
        state = await self._states.runtime_state(scope.company_id, OPERATIONS_AGENT_ID)
        if state.availability is AgentAvailability.DISABLED:
            raise OperationsAgentDisabledError()
        if not state.runnable:
            raise AgentNotRunnableError()
        await self.delegate.ensure_runnable(scope)

    async def run_chat(self, request: RequestContext, scope: ActionScope, message: str,
                       history: tuple[HistoryTurn, ...]) -> ChatRunResult:  # fmt: skip
        await self.ensure_runnable(scope)
        return await self.delegate.run_chat(request, scope, message, history)
