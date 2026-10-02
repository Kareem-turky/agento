"""Trusted Workflow Step handlers and the explicit runtime registry (Task 034).

A Step handler is trusted Product code bound to exactly one stable ``handler_id``:

* ``execute(context, run_input, checkpoints)`` performs the Step and returns a
  ``StepResult``: an outcome, an optional typed CHECKPOINT (persisted, size-bounded, the
  minimal state later Steps need) and an optional OUTPUT (ephemeral: returned to the
  current caller in memory, never persisted);
* ``verify(context, run_input, checkpoints, result)`` independently confirms the result.
  A Step is never ``succeeded`` merely because ``execute`` returned.

``run_input`` is the run's validated, typed input model (never raw request input) and
``checkpoints`` are the typed checkpoints of the Steps that already succeeded. Identity,
company and store come only from the trusted ``WorkflowStepContext``.

Two base classes fix the side-effect classification:

* ``ReadOnlyStepHandler``: reads only (bounded retries are allowed).
* ``GovernedWriteStepHandler``: its ``execute``/``verify`` are FINAL. The subclass only
  describes the governed action (``action``); the effect always goes through the existing
  ``ExecutionCoordinator`` (permission, policy, approval, execution, verification, audit).
  Only a VERIFIED ``ActionRun`` completes the Step; awaiting approval and requires-human
  stop the Workflow; a denial or a confirmed no-effect failure fails it.

``WorkflowRuntimeRegistry`` is built by trusted composition code from explicit
registrations: no handler comes from a database, an import path, user input or the
network. It validates every binding against the catalog and fails closed.
"""

from abc import ABC, abstractmethod
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Any, ClassVar
from uuid import UUID

from pydantic import BaseModel, ConfigDict, JsonValue

from app.context.models import ActorContext, RequestContext
from app.execution import ActionRun, ActionRunStatus, ExecutionCoordinator
from app.governance import ActionIntent, ActionScope
from app.workflow_management.catalog import ProductWorkflowCatalog
from app.workflow_management.definitions import CheckpointPolicy, StepSideEffect


class WorkflowStepContext(BaseModel):
    """Immutable trusted context of one Step attempt (built by the engine only)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    workflow_run_id: UUID
    workflow_id: str
    workflow_version: int
    step_id: str
    attempt: int
    request_id: UUID
    actor: ActorContext
    company_id: str
    store_id: str | None
    # The trusted request and scope, for Product services that require them
    # (governance, ExecutionCoordinator). Never built from Workflow input.
    request: RequestContext
    scope: ActionScope


class StepOutcome(StrEnum):
    COMPLETED = "completed"
    AWAITING_APPROVAL = "awaiting_approval"
    REQUIRES_HUMAN = "requires_human"


@dataclass(frozen=True, slots=True)
class StepResult:
    outcome: StepOutcome
    checkpoint: BaseModel | None = None
    output: Any = field(default=None, repr=False)  # ephemeral: never persisted or logged


class StepDeniedError(Exception):
    """A deterministic authorization denial: the Step fails (``access_denied``), never
    retried, and nothing was changed."""


class StepFailedError(Exception):
    """The Step failed with CONFIRMED no effect. ``retryable`` allows another attempt
    (read-only Steps only)."""

    def __init__(self, *, retryable: bool = False) -> None:
        super().__init__("workflow step failed")
        self.retryable = retryable


Checkpoints = Mapping[str, BaseModel]


class _StepHandler(ABC):
    handler_id: ClassVar[str]
    side_effect: ClassVar[StepSideEffect]
    # The typed checkpoint this Step produces (None: it produces none).
    checkpoint_model: ClassVar[type[BaseModel] | None] = None

    @abstractmethod
    async def execute(
        self, context: WorkflowStepContext, run_input: BaseModel, checkpoints: Checkpoints
    ) -> StepResult: ...

    @abstractmethod
    async def verify(
        self,
        context: WorkflowStepContext,
        run_input: BaseModel,
        checkpoints: Checkpoints,
        result: StepResult,
    ) -> bool: ...


class ReadOnlyStepHandler(_StepHandler, ABC):
    side_effect = StepSideEffect.READ_ONLY


class ActionRunCheckpoint(BaseModel):
    """What a governed write Step hands to later Steps: safe references only."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    action_run_id: UUID
    reference_id: str | None


class GovernedWriteStepHandler(_StepHandler, ABC):
    side_effect = StepSideEffect.GOVERNED_WRITE
    checkpoint_model = ActionRunCheckpoint

    def __init__(self, coordinator: ExecutionCoordinator) -> None:
        if not isinstance(coordinator, ExecutionCoordinator):
            raise TypeError("a governed write step requires the ExecutionCoordinator")
        self._coordinator = coordinator

    @abstractmethod
    def action(
        self, context: WorkflowStepContext, run_input: BaseModel, checkpoints: Checkpoints
    ) -> tuple[ActionIntent, Mapping[str, JsonValue]]:
        """The governed action and its raw parameters (validated by its ActionHandler)."""

    async def execute(
        self, context: WorkflowStepContext, run_input: BaseModel, checkpoints: Checkpoints
    ) -> StepResult:
        intent, parameters = self.action(context, run_input, checkpoints)
        outcome = await self._coordinator.run(context.request, intent, context.scope, parameters)
        if not isinstance(outcome, ActionRun):
            return StepResult(StepOutcome.REQUIRES_HUMAN)
        if outcome.status is ActionRunStatus.VERIFIED:
            reference = outcome.execution_result.reference_id if outcome.execution_result else None
            return StepResult(StepOutcome.COMPLETED, output=outcome,
                              checkpoint=ActionRunCheckpoint(action_run_id=outcome.run_id,
                                                             reference_id=reference))  # fmt: skip
        if outcome.status is ActionRunStatus.AWAITING_APPROVAL:
            return StepResult(StepOutcome.AWAITING_APPROVAL, output=outcome)
        if outcome.status is ActionRunStatus.DENIED:
            raise StepDeniedError()
        if outcome.status is ActionRunStatus.FAILED:
            raise StepFailedError(retryable=False)  # stopped safely with no side effect
        return StepResult(StepOutcome.REQUIRES_HUMAN, output=outcome)

    async def verify(
        self,
        context: WorkflowStepContext,
        run_input: BaseModel,
        checkpoints: Checkpoints,
        result: StepResult,
    ) -> bool:
        # Only an ActionRun VERIFIED by the coordinator (with a complete audit) counts.
        run = result.output
        return (
            isinstance(run, ActionRun)
            and run.status is ActionRunStatus.VERIFIED
            and run.audit_complete
            and isinstance(result.checkpoint, ActionRunCheckpoint)
            and result.checkpoint.action_run_id == run.run_id
        )


StepHandler = ReadOnlyStepHandler | GovernedWriteStepHandler


class WorkflowRegistrationError(ValueError):
    """The runtime bindings do not match the catalog (composition fails closed)."""


@dataclass(frozen=True, slots=True)
class WorkflowRuntimeRegistration:
    """Trusted binding of one Workflow: its typed input model and its Step handlers."""

    workflow_id: str
    input_model: type[BaseModel]
    handlers: tuple[StepHandler, ...]


class WorkflowRuntimeRegistry:
    """Immutable ``workflow_id -> (input model, handler_id -> handler)`` bindings.

    Every registered Workflow must exist in the catalog; every one of its Steps must be
    bound to exactly one handler whose side effect and checkpoint production match the
    Step; no handler may be unused or duplicated; governed write handlers must keep the
    final ExecutionCoordinator ``execute``/``verify``.
    """

    __slots__ = ("_bindings",)

    def __init__(
        self, catalog: ProductWorkflowCatalog, registrations: Iterable[WorkflowRuntimeRegistration]
    ) -> None:
        if not isinstance(catalog, ProductWorkflowCatalog):
            raise WorkflowRegistrationError("a ProductWorkflowCatalog is required")
        bindings: dict[str, tuple[type[BaseModel], Mapping[str, StepHandler]]] = {}
        for registration in registrations:
            if not isinstance(registration, WorkflowRuntimeRegistration):
                raise WorkflowRegistrationError("registrations must be WorkflowRuntimeRegistration")
            definition = catalog.get(registration.workflow_id)
            if definition is None:
                raise WorkflowRegistrationError("registration of an unknown workflow")
            if registration.workflow_id in bindings:
                raise WorkflowRegistrationError("duplicate workflow registration")
            model = registration.input_model
            if not (isinstance(model, type) and issubclass(model, BaseModel)
                    and model.model_config.get("frozen") and model.model_config.get("extra")
                    == "forbid"):  # fmt: skip
                raise WorkflowRegistrationError("the input model must be a frozen, strict model")
            handlers: dict[str, StepHandler] = {}
            for handler in registration.handlers:
                _check_handler(handler)
                if handler.handler_id in handlers:
                    raise WorkflowRegistrationError("duplicate handler id")
                handlers[handler.handler_id] = handler
            used = set()
            for step in definition.steps:
                handler = handlers.get(step.handler_id)
                if handler is None:
                    raise WorkflowRegistrationError("a step handler is not registered")
                if handler.side_effect is not step.side_effect:
                    raise WorkflowRegistrationError("handler side effect does not match its step")
                produces = handler.checkpoint_model is not None
                if produces != (step.checkpoint_policy is CheckpointPolicy.STATE):
                    raise WorkflowRegistrationError("handler checkpoint does not match its step")
                used.add(step.handler_id)
            if used != set(handlers):
                raise WorkflowRegistrationError("an unused handler is registered")
            bindings[registration.workflow_id] = (model, MappingProxyType(handlers))
        object.__setattr__(self, "_bindings", MappingProxyType(bindings))

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError("WorkflowRuntimeRegistry is immutable")

    def input_model(self, workflow_id: str) -> type[BaseModel] | None:
        binding = self._bindings.get(workflow_id)
        return None if binding is None else binding[0]

    def handler(self, workflow_id: str, handler_id: str) -> StepHandler | None:
        binding = self._bindings.get(workflow_id)
        return None if binding is None else binding[1].get(handler_id)

    @property
    def workflow_ids(self) -> frozenset[str]:
        return frozenset(self._bindings)

    def __contains__(self, workflow_id: object) -> bool:
        return workflow_id in self._bindings


def _check_handler(handler: object) -> None:
    if isinstance(handler, GovernedWriteStepHandler):
        # The effect may only go through the coordinator: execute/verify are final.
        if (type(handler).execute is not GovernedWriteStepHandler.execute
                or type(handler).verify is not GovernedWriteStepHandler.verify):  # fmt: skip
            raise WorkflowRegistrationError("a governed write step must use the coordinator")
        if handler.checkpoint_model is not ActionRunCheckpoint:
            raise WorkflowRegistrationError("a governed write step checkpoints its ActionRun")
    elif not isinstance(handler, ReadOnlyStepHandler):
        raise WorkflowRegistrationError("handlers must be trusted Workflow step handlers")
    if handler.side_effect is not (
        StepSideEffect.GOVERNED_WRITE
        if isinstance(handler, GovernedWriteStepHandler)
        else StepSideEffect.READ_ONLY
    ):
        raise WorkflowRegistrationError("a handler cannot change its side-effect class")
    handler_id = getattr(handler, "handler_id", None)
    if not isinstance(handler_id, str) or not handler_id:
        raise WorkflowRegistrationError("a handler must declare its handler id")
    model = handler.checkpoint_model
    if model is not None and not (
        isinstance(model, type)
        and issubclass(model, BaseModel)
        and model.model_config.get("frozen")
    ):
        raise WorkflowRegistrationError("a checkpoint model must be a frozen model")
