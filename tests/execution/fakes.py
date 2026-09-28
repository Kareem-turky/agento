"""Test-only deterministic handlers and audit sink (never used in production code)."""

import asyncio
from collections.abc import Coroutine, Mapping
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from app.context.models import ActorContext, RequestContext
from app.execution import (
    ActionExecutionContext,
    ActionHandlerRegistry,
    AuditEvent,
    AuditEventType,
    ExecutionCoordinator,
    ExecutionFailedWithoutEffect,
    ExecutionOutcomeUncertain,
    ExecutionResult,
    VerificationResult,
)
from app.governance import (
    ActionCatalog,
    ActionDefinition,
    ActionIntent,
    ActionRisk,
    ActionScope,
    ActionScopeRequirement,
    GovernanceGate,
)

COMPANY = "company-1"
STORE = "store-a"
SECRET_MARKER = "SENSITIVE-MARKER-must-not-be-recorded-0000"  # noqa: S105 - fake marker


def run[T](coro: Coroutine[Any, Any, T]) -> T:
    return asyncio.run(coro)


def _definition(name: str, risk: ActionRisk, scope=ActionScopeRequirement.STORE):
    return ActionDefinition(
        name=name,
        description=f"{name} (test)",
        risk=risk,
        required_permission=name,
        scope_requirement=scope,
    )


CATALOG = ActionCatalog(
    [
        _definition("notes.add", ActionRisk.LOW_RISK_WRITE),
        _definition("notes.read", ActionRisk.READ),
        _definition("orders.cancel", ActionRisk.MEDIUM_RISK),
        _definition("orders.refund", ActionRisk.HIGH_RISK),
        _definition("reports.rebuild", ActionRisk.LOW_RISK_WRITE, ActionScopeRequirement.COMPANY),
        _definition("labels.print", ActionRisk.LOW_RISK_WRITE),  # no handler registered
    ]
)


class NoteInput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    order_ref: str = Field(min_length=1, max_length=40)
    text: str = Field(min_length=1, max_length=200)


class FakeHandler:
    """Deterministic handler; counts calls and records what it received."""

    def __init__(
        self,
        action_name: str = "notes.add",
        *,
        execute_behaviour: str = "ok",
        verify_behaviour: str = "ok",
        validate_returns: Any = None,
        validate_model: type[BaseModel] | None = None,
    ) -> None:
        self._name = action_name
        self.execute_behaviour = execute_behaviour
        self.verify_behaviour = verify_behaviour
        self.validate_returns = validate_returns
        self.validate_model = validate_model or NoteInput
        self.validate_calls: list[Mapping[str, JsonValue]] = []
        self.execute_calls: list[Any] = []
        self.verify_calls: list[tuple[Any, ExecutionResult | None]] = []
        self.execute_contexts: list[ActionExecutionContext] = []
        self.verify_contexts: list[ActionExecutionContext] = []

    @property
    def action_name(self) -> str:
        return self._name

    def validate(self, parameters: Mapping[str, JsonValue]) -> BaseModel:
        self.validate_calls.append(parameters)
        if self.validate_returns is not None:
            return self.validate_returns
        return self.validate_model.model_validate(dict(parameters))

    async def execute(
        self, context: ActionExecutionContext, validated_input: BaseModel
    ) -> ExecutionResult:
        self.execute_contexts.append(context)
        self.execute_calls.append(validated_input)
        match self.execute_behaviour:
            case "ok":
                return ExecutionResult(reference_id="note-123")
            case "no_effect":
                raise ExecutionFailedWithoutEffect(f"provider said 400 {SECRET_MARKER}")
            case "uncertain":
                raise ExecutionOutcomeUncertain(f"timeout after send {SECRET_MARKER}")
            case "crash":
                raise RuntimeError(f"socket reset {SECRET_MARKER}")
            case "bad_result":
                return {"success": True, "raw": SECRET_MARKER}  # type: ignore[return-value]
        raise AssertionError(self.execute_behaviour)

    async def verify(
        self,
        context: ActionExecutionContext,
        validated_input: BaseModel,
        execution_result: ExecutionResult | None,
    ) -> VerificationResult:
        self.verify_contexts.append(context)
        self.verify_calls.append((validated_input, execution_result))
        match self.verify_behaviour:
            case "ok":
                return VerificationResult(verified=True, reason_code="note_present")
            case "mismatch":
                return VerificationResult(verified=False, reason_code="note_missing")
            case "crash":
                raise RuntimeError(f"re-read failed {SECRET_MARKER}")
            case "bad_result":
                return True  # type: ignore[return-value]
        raise AssertionError(self.verify_behaviour)


class RecordingAuditSink:
    """Collects events; can be told to fail on specific event types."""

    def __init__(self, fail_on: frozenset[AuditEventType] = frozenset()) -> None:
        self.events: list[AuditEvent] = []
        self.fail_on = fail_on
        self.attempts: list[AuditEventType] = []

    async def record(self, event: AuditEvent) -> None:
        self.attempts.append(event.event_type)
        if event.event_type in self.fail_on:
            raise ConnectionError(f"audit store down {SECRET_MARKER}")
        self.events.append(event)

    @property
    def types(self) -> list[AuditEventType]:
        return [e.event_type for e in self.events]


def actor(**overrides: Any) -> ActorContext:
    data: dict[str, Any] = {
        "actor_id": "user-1",
        "actor_type": "user",
        "company_id": COMPANY,
        "permissions": frozenset(CATALOG.names),
        "store_ids": frozenset({STORE}),
    }
    data.update(overrides)
    return ActorContext(**data)


def request(actor_ctx: ActorContext | None = None, **kwargs: Any) -> RequestContext:
    return RequestContext(
        request_id=UUID("00000000-0000-4000-8000-00000000000a"),
        actor=actor_ctx if actor_ctx is not None else actor(),
        **kwargs,
    )


def store_scope(store_id: str = STORE, company_id: str = COMPANY) -> ActionScope:
    return ActionScope(company_id=company_id, store_id=store_id)


VALID_PARAMS = {"order_ref": "ord-1", "text": "Customer called"}
FIXED_TIME = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)


def coordinator(*handlers: FakeHandler, sink: RecordingAuditSink | None = None, ids=None):
    sink = sink if sink is not None else RecordingAuditSink()
    return (
        ExecutionCoordinator(
            GovernanceGate(CATALOG),
            ActionHandlerRegistry(handlers),
            sink,
            clock=lambda: FIXED_TIME,
            **({"id_factory": ids} if ids else {}),
        ),
        sink,
    )


def execute(
    coord: ExecutionCoordinator, name: str = "notes.add", params=None, req=None, scope=None
):
    return run(
        coord.run(
            req if req is not None else request(),
            ActionIntent(name=name),
            scope if scope is not None else store_scope(),
            params if params is not None else VALID_PARAMS,
        )
    )
