"""Trusted action handlers and their immutable registry.

A handler is backend code bound to exactly one governed action name:

- ``validate(parameters)`` turns untrusted raw parameters into a trusted, immutable
  input model. No side effects.
- ``execute(context, validated_input)`` is the ONLY side-effect boundary. It returns a
  safe ``ExecutionResult`` or raises an ``ActionExecutionError``.
- ``verify(context, validated_input, execution_result)`` independently confirms the intended
  effect (a re-read of the external system), never by trusting ``execute``'s answer.
  ``execution_result`` is the safe receipt of a completed execute, or ``None`` when
  execute was attempted but its outcome is uncertain (timeout, crash, invalid
  return). With ``None`` the verifier inspects the target state from the validated
  input alone; its answer is recovery evidence and never makes the run VERIFIED.

``context`` is the trusted ``ActionExecutionContext`` built by the coordinator (run,
request, actor, company, store, channel). It is the only source of scope and
identity: handlers never take company, store or actor from raw parameters.
"""

from collections.abc import Iterable, Mapping
from types import MappingProxyType
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, JsonValue

from app.execution.context import ActionExecutionContext
from app.execution.models import ExecutionResult, VerificationResult

RawParameters = Mapping[str, JsonValue]


@runtime_checkable
class ActionHandler(Protocol):
    @property
    def action_name(self) -> str: ...

    def validate(self, parameters: RawParameters) -> BaseModel: ...

    async def execute(
        self, context: ActionExecutionContext, validated_input: BaseModel
    ) -> ExecutionResult: ...

    async def verify(
        self,
        context: ActionExecutionContext,
        validated_input: BaseModel,
        execution_result: ExecutionResult | None,
    ) -> VerificationResult: ...


class ActionHandlerRegistry:
    """Immutable mapping of governed action name -> trusted handler.

    Built once by backend code and injected; there is no global registry. Unknown
    names return ``None`` (the coordinator fails closed).
    """

    def __init__(self, handlers: Iterable[ActionHandler]) -> None:
        by_name: dict[str, ActionHandler] = {}
        for handler in handlers:
            if not isinstance(handler, ActionHandler):
                raise TypeError("an ActionHandlerRegistry holds ActionHandler objects only")
            name = handler.action_name
            if not isinstance(name, str) or not name:
                raise ValueError("a handler must declare a non-empty action_name")
            if name in by_name:
                raise ValueError(f"duplicate handler for action: {name}")
            by_name[name] = handler
        self._handlers: Mapping[str, ActionHandler] = MappingProxyType(by_name)

    def get(self, action_name: str) -> ActionHandler | None:
        return self._handlers.get(action_name)

    def __contains__(self, action_name: object) -> bool:
        return action_name in self._handlers

    def __len__(self) -> int:
        return len(self._handlers)

    @property
    def action_names(self) -> frozenset[str]:
        return frozenset(self._handlers)
