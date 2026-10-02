"""``WorkflowEngine``: deterministic, durable, in-process execution of Product Workflows.

    execute(workflow_id, trusted request, trusted scope, input)
      -> catalog definition + runtime registration     (else WorkflowUnavailableError)
      -> trusted actor, scope inside the actor company (else WorkflowAccessDeniedError)
      -> typed input model validation                  (else WorkflowInputInvalidError)
      -> create run ``pending`` + workflow_requested   (one transaction)
      -> claim (fresh token) -> ``running`` + workflow_started
      -> for each Step IN ORDER (sequential; completed Steps are never re-run):
           start attempt (renews the claim) + step_started
           -> asyncio timeout: execute -> verify
           -> typed, size-bounded checkpoint
           -> finish attempt (+ events, + terminal run state) in ONE transaction
      -> WorkflowRunResult (status, safe failure code, EPHEMERAL Step outputs)

    resume(trusted request, run_id): the same loop on a stored run (company scoped). An
    attempt left ``running`` by a lost executor is closed first: ``executor_lost`` for a
    read-only Step (another attempt may follow, within ``max_attempts``), and
    ``requires_human`` for a governed write (its effect may exist: never retried).

Failure semantics (never improvised by a model; no LLM is involved anywhere here):

* read-only Step: an unexpected error or timeout is retried immediately while attempts
  remain (each attempt is a durable row); a denial, a confirmed failure, a verification
  failure or an invalid checkpoint stops the run ``failed``;
* governed write Step: runs once. Approval required -> ``awaiting_approval`` (stops; no
  later Step, no auto-approval). An uncertain outcome, a timeout, an error, a failed
  verification or an invalid checkpoint -> ``requires_human``;
* durable state not guaranteed (any repository failure) -> stop immediately
  (``WorkflowUnavailableError``); a lost claim -> stop without writing
  (``WorkflowLeaseConflictError``).

No transaction is held while Step code runs. Nothing is scheduled, queued or detached:
the caller awaits the run; durable state makes an interrupted run resumable.
"""

import asyncio
import hashlib
import json
import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from types import MappingProxyType
from typing import Any, NoReturn
from uuid import UUID, uuid4

from pydantic import BaseModel, ValidationError

from app.context.models import ActorContext, RequestContext
from app.governance import ActionScope
from app.observability.contracts import (
    ObservationDetails,
    ObservationOutcome,
    ProductObservability,
    ProductOperation,
    WorkflowDetails,
    observe,
)
from app.workflow_management.catalog import ProductWorkflowCatalog
from app.workflow_management.contracts import (
    AttemptFinish,
    Claim,
    RunChange,
    WorkflowClaimConflictError,
    WorkflowRepositoryError,
    WorkflowRunRepository,
)
from app.workflow_management.definitions import (
    CheckpointPolicy,
    StepSideEffect,
    WorkflowDefinition,
    WorkflowStepDefinition,
)
from app.workflow_management.errors import (
    WorkflowAccessDeniedError,
    WorkflowInputInvalidError,
    WorkflowLeaseConflictError,
    WorkflowNotResumableError,
    WorkflowRunNotFoundError,
    WorkflowUnavailableError,
)
from app.workflow_management.handlers import (
    StepDeniedError,
    StepFailedError,
    StepHandler,
    StepOutcome,
    StepResult,
    WorkflowRuntimeRegistry,
    WorkflowStepContext,
)
from app.workflow_management.records import (
    MAX_CHECKPOINT_BYTES,
    MAX_INPUT_BYTES,
    NewWorkflowEvent,
    StepAttemptRecord,
    WorkflowRunRecord,
    canonical_json,
)
from app.workflow_management.state import (
    ACTIVE_RUN_STATUSES,
    RUN_STATUS_FOR_STOPPED_STEP,
    TERMINAL_RUN_EVENT,
    InvalidTransitionError,
    StepAttemptStatus,
    VerificationCode,
    WorkflowEventType,
    WorkflowFailureCode,
    WorkflowRunStatus,
    check_run_transition,
    check_step_transition,
)

WORKFLOW_LOGGER_NAME = "app.product.workflows"
DEFAULT_LEASE_MARGIN_SECONDS = 30

E = WorkflowEventType
F = WorkflowFailureCode
R = WorkflowRunStatus
S = StepAttemptStatus


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class WorkflowRunResult:
    """What the caller gets back. ``outputs`` holds the EPHEMERAL outputs of the Steps
    completed by THIS invocation (in memory only; never persisted)."""

    run_id: UUID
    workflow_id: str
    status: WorkflowRunStatus
    failure_code: WorkflowFailureCode | None
    outputs: Mapping[str, Any] = field(default_factory=dict, repr=False)

    def output(self, step_id: str) -> Any:
        return self.outputs.get(step_id)


@dataclass(frozen=True, slots=True)
class _Decision:
    """How one finished attempt is recorded and what happens next."""

    attempt_status: StepAttemptStatus
    failure_code: WorkflowFailureCode | None
    verification_code: VerificationCode | None
    retryable: bool = False
    stop_code: WorkflowFailureCode | None = None  # the run's code if the run stops here


class _StopRun(Exception):
    def __init__(self, result: WorkflowRunResult) -> None:
        super().__init__("workflow run stopped")
        self.result = result


class _Run:
    """Per-invocation state of one claimed run."""

    def __init__(
        self,
        engine: "WorkflowEngine",
        definition: WorkflowDefinition,
        record: WorkflowRunRecord,
        claim: Claim,
        request: RequestContext,
        scope: ActionScope,
        actor: ActorContext,
        run_input: BaseModel,
    ) -> None:
        self.engine = engine
        self.definition = definition
        self.record = record
        self.claim = claim
        self.request = request
        self.scope = scope
        self.actor = actor
        self.run_input = run_input
        self.status = record.status
        self.checkpoints: dict[str, BaseModel] = {}
        self.outputs: dict[str, Any] = {}

    @property
    def workflow_id(self) -> str:
        return self.definition.workflow_id

    def result(
        self, status: WorkflowRunStatus, code: WorkflowFailureCode | None
    ) -> WorkflowRunResult:
        return WorkflowRunResult(self.record.run_id, self.workflow_id, status, code,
                                 MappingProxyType(dict(self.outputs)))  # fmt: skip

    async def advance(self, change: RunChange) -> None:
        try:
            check_run_transition(self.status, change.status)
            if change.finish_attempt is not None:
                check_step_transition(S.RUNNING, change.finish_attempt.status)
            self.record = await self.engine._repository.advance(self.claim, change)
        except WorkflowClaimConflictError:
            raise WorkflowLeaseConflictError() from None
        except (WorkflowRepositoryError, InvalidTransitionError, ValidationError):
            raise WorkflowUnavailableError() from None
        self.status = change.status


class WorkflowEngine:
    """Executes registered Product Workflows (internal Product orchestration: there is
    no public HTTP run endpoint). Authorization stays with the Steps' own Product
    governance; the engine only binds the trusted identity and scope to the run."""

    def __init__(
        self,
        catalog: ProductWorkflowCatalog,
        registry: WorkflowRuntimeRegistry,
        repository: WorkflowRunRepository,
        *,
        observability: ProductObservability | None = None,
        clock: Callable[[], datetime] = _utc_now,
        new_id: Callable[[], UUID] = uuid4,
        lease_margin_seconds: int = DEFAULT_LEASE_MARGIN_SECONDS,
        logger: logging.Logger | None = None,
    ) -> None:
        if not isinstance(catalog, ProductWorkflowCatalog):
            raise TypeError("a ProductWorkflowCatalog is required")
        if not isinstance(registry, WorkflowRuntimeRegistry):
            raise TypeError("a WorkflowRuntimeRegistry is required")
        if not isinstance(repository, WorkflowRunRepository):
            raise TypeError("a WorkflowRunRepository is required")
        if registry.workflow_ids - catalog.workflow_ids:
            raise ValueError("the registry binds workflows outside the catalog")
        if not 1 <= lease_margin_seconds <= 3600:
            raise ValueError("lease_margin_seconds must be between 1 and 3600")
        self._catalog = catalog
        self._registry = registry
        self._repository = repository
        # The application's ONE Product observability, injected by the composition root
        # (``app.bootstrap`` passes the instance ``create_app`` uses). ``None``: Workflow
        # runs are not observed. The engine never builds an observability of its own.
        self._observability = observability
        self._clock = clock
        self._new_id = new_id
        self._margin = timedelta(seconds=lease_margin_seconds)
        self._logger = logger if logger is not None else logging.getLogger(WORKFLOW_LOGGER_NAME)

    @property
    def catalog(self) -> ProductWorkflowCatalog:
        return self._catalog

    @property
    def registry(self) -> WorkflowRuntimeRegistry:
        return self._registry

    # ----- entry points -----------------------------------------------------------------------

    async def execute(
        self,
        workflow_id: str,
        request: RequestContext,
        scope: ActionScope,
        run_input: BaseModel | Mapping[str, Any],
    ) -> WorkflowRunResult:
        definition = self._catalog.get(workflow_id) if isinstance(workflow_id, str) else None
        model = self._registry.input_model(workflow_id) if definition is not None else None
        request_id = getattr(request, "request_id", None)
        with observe(self._observability, ProductOperation.WORKFLOW_RUN, request_id) as obs:
            try:
                if definition is None or model is None:
                    raise WorkflowUnavailableError()
                actor = self._trusted_actor(request, scope)
                validated, state, fingerprint = self._validate_input(definition, model, run_input)
                now = self._now()
                record = WorkflowRunRecord(
                    run_id=self._new_id(), workflow_id=definition.workflow_id,
                    workflow_version=definition.version, request_id=request.request_id,
                    company_id=actor.company_id, actor_id=actor.actor_id,
                    actor_type=actor.actor_type, channel=request.channel,
                    store_id=scope.store_id, status=R.PENDING, current_step_id=None,
                    failure_code=None, input_state=state, input_fingerprint=fingerprint,
                    lease_owner=None, lease_expires_at=None, created_at=now, updated_at=now,
                    completed_at=None,
                )  # fmt: skip
                try:
                    await self._repository.create_run(
                        record, NewWorkflowEvent(event_type=E.WORKFLOW_REQUESTED,
                                                 status=R.PENDING, occurred_at=now))  # fmt: skip
                except WorkflowRepositoryError:
                    raise WorkflowUnavailableError() from None
                result = await self._drive(definition, record, request, scope, actor, validated,
                                           resumed=False)  # fmt: skip
            except Exception as error:
                obs.finish(_error_outcome(error))
                raise
            obs.finish(_run_outcome(result), _run_details(result))
            return result

    async def resume(self, request: RequestContext, run_id: UUID) -> WorkflowRunResult:
        """Continue an interrupted run of the caller's company from durable state.

        Completed Steps are never executed again: their checkpoints are reloaded."""
        with observe(self._observability, ProductOperation.WORKFLOW_RUN,
                     getattr(request, "request_id", None)) as obs:  # fmt: skip
            try:
                actor = self._trusted_actor(request, None)
                try:
                    record = await self._repository.get_run(actor.company_id, run_id)
                except WorkflowRepositoryError:
                    raise WorkflowUnavailableError() from None
                if record is None:
                    raise WorkflowRunNotFoundError()
                if record.status not in ACTIVE_RUN_STATUSES:
                    raise WorkflowNotResumableError()
                definition = self._catalog.get(record.workflow_id)
                model = self._registry.input_model(record.workflow_id)
                if definition is None or model is None:
                    raise WorkflowUnavailableError()
                if definition.version != record.workflow_version:
                    raise WorkflowNotResumableError()  # never run a changed definition
                try:
                    validated: BaseModel | None = model.model_validate_json(
                        canonical_json(record.input_state)
                    )
                except ValidationError:
                    validated = None  # stored input is invalid: fail closed once claimed
                scope = ActionScope(company_id=record.company_id, store_id=record.store_id)
                result = await self._drive(definition, record, request, scope, actor, validated,
                                           resumed=True)  # fmt: skip
            except Exception as error:
                obs.finish(_error_outcome(error))
                raise
            obs.finish(_run_outcome(result), _run_details(result))
            return result

    # ----- validation -------------------------------------------------------------------------

    def _now(self) -> datetime:
        now = self._clock()
        if not isinstance(now, datetime) or now.utcoffset() is None:
            raise WorkflowUnavailableError()  # a naive clock fails closed
        return now

    @staticmethod
    def _trusted_actor(request: RequestContext, scope: ActionScope | None) -> ActorContext:
        if not isinstance(request, RequestContext) or request.actor is None:
            raise WorkflowAccessDeniedError()
        actor = request.actor
        if scope is not None and (
            not isinstance(scope, ActionScope) or scope.company_id != actor.company_id
        ):
            raise WorkflowAccessDeniedError()  # never record a run in another company
        return actor

    @staticmethod
    def _validate_input(
        definition: WorkflowDefinition,
        model: type[BaseModel],
        run_input: BaseModel | Mapping[str, Any],
    ) -> tuple[BaseModel, dict[str, Any], str]:
        try:
            if isinstance(run_input, BaseModel):
                if type(run_input) is not model:
                    raise WorkflowInputInvalidError()
                state = run_input.model_dump(mode="json")
            elif isinstance(run_input, Mapping):
                state = model.model_validate(dict(run_input)).model_dump(mode="json")
            else:
                raise WorkflowInputInvalidError()
            text = canonical_json(state)
            if len(text.encode()) > MAX_INPUT_BYTES:
                raise WorkflowInputInvalidError()
            # Execute exactly what a resumed run will see: the stored JSON form.
            validated = model.model_validate_json(text)
        except (ValidationError, TypeError, ValueError):
            raise WorkflowInputInvalidError() from None
        digest = hashlib.sha256(
            canonical_json([definition.workflow_id, definition.version, state]).encode()
        ).hexdigest()
        return validated, state, digest

    # ----- the run loop -----------------------------------------------------------------------

    async def _drive(
        self,
        definition: WorkflowDefinition,
        record: WorkflowRunRecord,
        request: RequestContext,
        scope: ActionScope,
        actor: ActorContext,
        run_input: BaseModel | None,
        *,
        resumed: bool,
    ) -> WorkflowRunResult:
        token = self._new_id()
        now = self._now()
        try:
            claimed = await self._repository.claim(record.company_id, record.run_id, token, now,
                                                   now + self._margin)  # fmt: skip
        except LookupError:
            raise WorkflowRunNotFoundError() from None
        except WorkflowClaimConflictError:
            raise WorkflowLeaseConflictError() from None
        except WorkflowRepositoryError:
            raise WorkflowUnavailableError() from None
        claim = Claim(run_id=claimed.run_id, company_id=claimed.company_id, token=token)
        run = _Run(self, definition, claimed, claim, request, scope, actor,
                   run_input if run_input is not None else _NO_INPUT)  # fmt: skip
        try:
            if run_input is None:
                await self._terminate(run, R.FAILED, F.INPUT_INVALID, None, start=True)
            started = E.WORKFLOW_RESUMED if resumed else E.WORKFLOW_STARTED
            await run.advance(RunChange(
                expected_status=run.status, status=R.RUNNING,
                current_step_id=run.record.current_step_id,
                lease_expires_at=self._now() + self._margin, updated_at=self._now(),
                events=(NewWorkflowEvent(event_type=started, status=R.RUNNING,
                                         occurred_at=self._now()),),
            ))  # fmt: skip
            self._log("workflow_started" if not resumed else "workflow_resumed", run)
            try:
                attempts = await self._repository.list_attempts(record.company_id, record.run_id)
            except WorkflowRepositoryError:
                raise WorkflowUnavailableError() from None
            steps = definition.steps
            for index, step in enumerate(steps):
                next_step = steps[index + 1].step_id if index + 1 < len(steps) else None
                previous = sorted((a for a in attempts if a.step_id == step.step_id),
                                  key=lambda a: a.attempt)  # fmt: skip
                await self._run_step(run, step, previous, next_step)
            return run.result(R.SUCCEEDED, None)
        except _StopRun as stop:
            return stop.result

    async def _run_step(
        self,
        run: _Run,
        step: WorkflowStepDefinition,
        previous: list[StepAttemptRecord],
        next_step: str | None,
    ) -> None:
        handler = self._registry.handler(run.workflow_id, step.handler_id)
        if handler is None:
            await self._terminate(run, R.FAILED, F.HANDLER_NOT_REGISTERED, step.step_id)
        done = next((a for a in previous if a.status is S.SUCCEEDED), None)
        if done is not None:  # completed before (this or an earlier process): never re-run
            checkpoint = self._load_checkpoint(handler, step, done)
            if isinstance(checkpoint, _Invalid):
                await self._terminate(run, R.FAILED, F.CHECKPOINT_INVALID, step.step_id)
            if isinstance(checkpoint, BaseModel):
                run.checkpoints[step.step_id] = checkpoint
            return
        if any(a.status not in (S.RUNNING, S.FAILED, S.TIMED_OUT) for a in previous):
            # A stopped attempt can only belong to a terminal run: never continue past it.
            await self._terminate(run, R.FAILED, F.WORKFLOW_UNAVAILABLE, step.step_id)
        used = len(previous)
        orphan = previous[-1] if previous and previous[-1].status is S.RUNNING else None
        if orphan is not None:
            await self._close_orphan(run, step, orphan, attempts_left=used < step.max_attempts)
        while True:
            used += 1
            if await self._attempt(run, step, handler, used, next_step):
                return

    async def _close_orphan(
        self, run: _Run, step: WorkflowStepDefinition, orphan: StepAttemptRecord, *,
        attempts_left: bool,
    ) -> None:  # fmt: skip
        """The attempt of a lost executor (its claim expired and was taken over)."""
        now = self._now()
        if step.side_effect is StepSideEffect.GOVERNED_WRITE:
            decision = _Decision(S.REQUIRES_HUMAN, F.EXECUTOR_LOST, None,
                                 stop_code=F.STEP_OUTCOME_UNCERTAIN)  # fmt: skip
        else:
            decision = _Decision(S.FAILED, F.EXECUTOR_LOST, None, retryable=True,
                                 stop_code=F.EXECUTOR_LOST)  # fmt: skip
        await self._finish(run, step, orphan.attempt, decision, now, checkpoint=None,
                           next_step=step.step_id, attempts_left=attempts_left)  # fmt: skip

    async def _attempt(
        self,
        run: _Run,
        step: WorkflowStepDefinition,
        handler: StepHandler,
        attempt: int,
        next_step: str | None,
    ) -> bool:
        """Run one attempt. True: the Step succeeded. False: retry. Stops raise."""
        start = self._now()
        record = StepAttemptRecord(
            run_id=run.record.run_id, company_id=run.record.company_id, step_id=step.step_id,
            attempt=attempt, handler_id=step.handler_id, status=S.RUNNING, failure_code=None,
            verification_code=None, checkpoint=None, started_at=start, completed_at=None,
        )  # fmt: skip
        await run.advance(RunChange(
            expected_status=R.RUNNING, status=R.RUNNING, current_step_id=step.step_id,
            lease_expires_at=start + timedelta(seconds=step.timeout_seconds) + self._margin,
            updated_at=start, start_attempt=record,
            events=(NewWorkflowEvent(event_type=E.STEP_STARTED, step_id=step.step_id,
                                     attempt=attempt, status=S.RUNNING, occurred_at=start),),
        ))  # fmt: skip
        context = WorkflowStepContext(
            workflow_run_id=run.record.run_id, workflow_id=run.workflow_id,
            workflow_version=run.definition.version, step_id=step.step_id, attempt=attempt,
            request_id=run.request.request_id, actor=run.actor, company_id=run.record.company_id,
            store_id=run.record.store_id, request=run.request, scope=run.scope,
        )  # fmt: skip
        attempts_left = attempt < step.max_attempts
        with observe(self._observability, ProductOperation.WORKFLOW_STEP_ATTEMPT,
                     run.request.request_id) as obs:  # fmt: skip
            decision, checkpoint, output = await self._perform(run, step, handler, context)
            retrying = decision.retryable and attempts_left
            obs.finish(
                ObservationOutcome.COMPLETED if decision.attempt_status is S.SUCCEEDED
                else ObservationOutcome.DENIED if decision.failure_code is F.ACCESS_DENIED
                else ObservationOutcome.ERROR,
                ObservationDetails(workflow=WorkflowDetails(
                    workflow_id=run.workflow_id, step_id=step.step_id,
                    status=decision.attempt_status, failure_code=decision.failure_code,
                    retry=retrying)),
            )  # fmt: skip
        succeeded = await self._finish(run, step, attempt, decision, self._now(),
                                       checkpoint=checkpoint, next_step=next_step,
                                       attempts_left=attempts_left)  # fmt: skip
        if succeeded:
            if isinstance(checkpoint, BaseModel):
                run.checkpoints[step.step_id] = checkpoint
            run.outputs[step.step_id] = output
        return succeeded

    async def _perform(
        self,
        run: _Run,
        step: WorkflowStepDefinition,
        handler: StepHandler,
        context: WorkflowStepContext,
    ) -> tuple[_Decision, BaseModel | None, Any]:
        write = step.side_effect is StepSideEffect.GOVERNED_WRITE
        # An effect may exist after a write Step started: anything unclear needs a human.
        unclear = (S.REQUIRES_HUMAN, F.STEP_OUTCOME_UNCERTAIN) if write else None
        checkpoints = MappingProxyType(dict(run.checkpoints))
        verifying = False
        try:
            async with asyncio.timeout(step.timeout_seconds):
                result = await handler.execute(context, run.run_input, checkpoints)
                if not isinstance(result, StepResult) or not isinstance(result.outcome,
                                                                        StepOutcome):  # fmt: skip
                    raise TypeError("invalid step result")
                if result.outcome is StepOutcome.AWAITING_APPROVAL:
                    return _Decision(S.AWAITING_APPROVAL, F.APPROVAL_REQUIRED, None,
                                     stop_code=F.APPROVAL_REQUIRED), None, None  # fmt: skip
                if result.outcome is StepOutcome.REQUIRES_HUMAN:
                    return _Decision(S.REQUIRES_HUMAN, F.STEP_OUTCOME_UNCERTAIN, None,
                                     stop_code=F.STEP_OUTCOME_UNCERTAIN), None, None  # fmt: skip
                verifying = True
                verified = await handler.verify(context, run.run_input, checkpoints, result)
        except TimeoutError:
            if write:
                return _Decision(S.TIMED_OUT, F.STEP_TIMEOUT, None,
                                 stop_code=F.STEP_OUTCOME_UNCERTAIN), None, None  # fmt: skip
            return _Decision(S.TIMED_OUT, F.STEP_TIMEOUT, None, retryable=True,
                             stop_code=F.STEP_TIMEOUT), None, None  # fmt: skip
        except StepDeniedError:
            return _Decision(S.FAILED, F.ACCESS_DENIED, None, stop_code=F.ACCESS_DENIED), None, None
        except StepFailedError as error:
            return _Decision(S.FAILED, F.STEP_EXECUTION_FAILED, None,
                             retryable=error.retryable and not write,
                             stop_code=F.STEP_EXECUTION_FAILED), None, None  # fmt: skip
        except Exception:  # noqa: BLE001 - classified by a stable code; never recorded
            if verifying:
                code = F.STEP_VERIFICATION_FAILED
                if write:
                    return _Decision(S.REQUIRES_HUMAN, code, VerificationCode.NOT_VERIFIED,
                                     stop_code=code), None, None  # fmt: skip
                return _Decision(S.FAILED, code, VerificationCode.NOT_VERIFIED,
                                 stop_code=code), None, None  # fmt: skip
            if unclear is not None:
                return _Decision(unclear[0], unclear[1], None, stop_code=unclear[1]), None, None
            return _Decision(S.FAILED, F.STEP_EXECUTION_FAILED, None, retryable=True,
                             stop_code=F.STEP_EXECUTION_FAILED), None, None  # fmt: skip
        if verified is not True:
            code = F.STEP_VERIFICATION_FAILED
            status = S.REQUIRES_HUMAN if write else S.FAILED
            return (
                _Decision(status, code, VerificationCode.NOT_VERIFIED, stop_code=code),
                None,
                None,
            )
        checkpoint = self._produce_checkpoint(handler, step, result.checkpoint)
        if isinstance(checkpoint, _Invalid):
            code = F.CHECKPOINT_INVALID
            status = S.REQUIRES_HUMAN if write else S.FAILED
            return _Decision(status, code, VerificationCode.VERIFIED, stop_code=code), None, None
        return (_Decision(S.SUCCEEDED, None, VerificationCode.VERIFIED),
                checkpoint, result.output)  # fmt: skip

    async def _finish(
        self,
        run: _Run,
        step: WorkflowStepDefinition,
        attempt: int,
        decision: _Decision,
        now: datetime,
        *,
        checkpoint: BaseModel | None,
        next_step: str | None,
        attempts_left: bool,
    ) -> bool:
        """Record the attempt's end (and the run's, if it stops) in ONE transaction."""
        status = decision.attempt_status
        events: list[NewWorkflowEvent] = []

        def event(event_type: WorkflowEventType, **fields: Any) -> None:
            events.append(NewWorkflowEvent(event_type=event_type, step_id=step.step_id,
                                           attempt=attempt, occurred_at=now, **fields))  # fmt: skip

        stored = None if checkpoint is None else checkpoint.model_dump(mode="json")
        finish = AttemptFinish(step_id=step.step_id, attempt=attempt, status=status,
                               failure_code=decision.failure_code,
                               verification_code=decision.verification_code,
                               checkpoint=stored, completed_at=now)  # fmt: skip
        if status is S.SUCCEEDED:
            event(E.STEP_SUCCEEDED, status=status)
            done = next_step is None
            if done:
                event(E.WORKFLOW_SUCCEEDED, status=R.SUCCEEDED)
            await run.advance(RunChange(
                expected_status=R.RUNNING, status=R.SUCCEEDED if done else R.RUNNING,
                current_step_id=None if done else next_step,
                lease_expires_at=None if done else now + self._margin, updated_at=now,
                completed_at=now if done else None, finish_attempt=finish, events=tuple(events),
            ))  # fmt: skip
            self._log("step_succeeded", run, step.step_id, attempt, status)
            if done:
                self._log("workflow_succeeded", run, status=R.SUCCEEDED)
            return True

        if status is not S.AWAITING_APPROVAL:
            event(E.STEP_TIMED_OUT if status is S.TIMED_OUT else E.STEP_FAILED, status=status,
                  failure_code=decision.failure_code)  # fmt: skip
        if decision.retryable and attempts_left:
            event(E.STEP_RETRYING, status=status, failure_code=decision.failure_code)
            await run.advance(RunChange(
                expected_status=R.RUNNING, status=R.RUNNING, current_step_id=step.step_id,
                lease_expires_at=now + self._margin, updated_at=now, finish_attempt=finish,
                events=tuple(events),
            ))  # fmt: skip
            self._log("step_retrying", run, step.step_id, attempt, status, decision.failure_code)
            return False

        run_status = RUN_STATUS_FOR_STOPPED_STEP[status]
        if (
            step.side_effect is StepSideEffect.GOVERNED_WRITE
            and run_status is R.FAILED
            and (decision.stop_code is F.STEP_OUTCOME_UNCERTAIN)
        ):
            run_status = R.REQUIRES_HUMAN  # a write timeout: the effect may exist
        code = decision.stop_code
        if decision.retryable and attempt > 1:
            code = F.RETRY_EXHAUSTED
        self._log("step_stopped", run, step.step_id, attempt, status, decision.failure_code)
        await self._terminate(run, run_status, code, step.step_id, finish=finish,
                              extra_events=tuple(events), now=now)  # fmt: skip

    async def _terminate(
        self,
        run: _Run,
        status: WorkflowRunStatus,
        code: WorkflowFailureCode | None,
        step_id: str | None,
        *,
        finish: AttemptFinish | None = None,
        extra_events: tuple[NewWorkflowEvent, ...] = (),
        now: datetime | None = None,
        start: bool = False,
    ) -> NoReturn:
        now = now if now is not None else self._now()
        terminal_event = NewWorkflowEvent(
            event_type=TERMINAL_RUN_EVENT[status],
            step_id=step_id,
            status=status,
            failure_code=code,
            occurred_at=now,
        )
        await run.advance(RunChange(
            expected_status=run.status, status=status,
            current_step_id=step_id if step_id is not None else run.record.current_step_id,
            failure_code=code, lease_expires_at=None, updated_at=now, completed_at=now,
            finish_attempt=finish, events=(*extra_events, terminal_event),
        ))  # fmt: skip
        self._log("workflow_stopped" if not start else "workflow_rejected", run, step_id,
                  status=status, code=code)  # fmt: skip
        raise _StopRun(run.result(status, code))

    # ----- checkpoints ------------------------------------------------------------------------

    @staticmethod
    def _produce_checkpoint(
        handler: StepHandler, step: WorkflowStepDefinition, checkpoint: object
    ) -> "BaseModel | _Invalid | None":
        model = handler.checkpoint_model
        if step.checkpoint_policy is CheckpointPolicy.NONE or model is None:
            return None if checkpoint is None else _INVALID
        if checkpoint is None or type(checkpoint) is not model:
            return _INVALID
        try:
            text = canonical_json(checkpoint.model_dump(mode="json"))
            if len(text.encode()) > MAX_CHECKPOINT_BYTES:
                return _INVALID
            # The checkpoint later Steps (and a resumed run) will see: the stored form.
            return model.model_validate_json(text)
        except (ValidationError, TypeError, ValueError):
            return _INVALID

    @staticmethod
    def _load_checkpoint(
        handler: StepHandler, step: WorkflowStepDefinition, attempt: StepAttemptRecord
    ) -> "BaseModel | _Invalid | None":
        model = handler.checkpoint_model
        if step.checkpoint_policy is CheckpointPolicy.NONE or model is None:
            return None if attempt.checkpoint is None else _INVALID
        if attempt.checkpoint is None:
            return _INVALID
        try:
            return model.model_validate_json(canonical_json(attempt.checkpoint))
        except (ValidationError, TypeError, ValueError):
            return _INVALID

    # ----- logging (safe metadata only) -------------------------------------------------------

    def _log(
        self,
        event: str,
        run: _Run,
        step_id: str | None = None,
        attempt: int | None = None,
        status: object = None,
        code: WorkflowFailureCode | None = None,
    ) -> None:
        body = {"event": event, "workflow_id": run.workflow_id, "run_id": str(run.record.run_id),
                "step_id": step_id, "attempt": attempt,
                "status": str(status) if status is not None else None,
                "failure_code": code.value if code is not None else None}  # fmt: skip
        try:
            self._logger.info(json.dumps(body, sort_keys=True, separators=(",", ":")))
        except Exception:  # noqa: BLE001, S110 - logging never affects execution
            pass


class _Invalid:
    pass


_INVALID = _Invalid()


class _NoInput(BaseModel):
    """Stands in for an invalid stored input: the run is stopped before any Step."""


_NO_INPUT = _NoInput()


def _run_outcome(result: WorkflowRunResult) -> ObservationOutcome:
    if result.status in (R.SUCCEEDED, R.AWAITING_APPROVAL):
        return ObservationOutcome.COMPLETED
    if result.failure_code is F.ACCESS_DENIED:
        return ObservationOutcome.DENIED
    if result.failure_code is F.INPUT_INVALID:
        return ObservationOutcome.INVALID
    return ObservationOutcome.ERROR


def _run_details(result: WorkflowRunResult) -> ObservationDetails:
    return ObservationDetails(
        workflow=WorkflowDetails(
            workflow_id=result.workflow_id, status=result.status, failure_code=result.failure_code
        )
    )


def _error_outcome(error: Exception) -> ObservationOutcome:
    if isinstance(error, WorkflowAccessDeniedError):
        return ObservationOutcome.DENIED
    if isinstance(error, WorkflowInputInvalidError):
        return ObservationOutcome.INVALID
    if isinstance(error, WorkflowLeaseConflictError):
        return ObservationOutcome.CONFLICT
    if isinstance(error, WorkflowRunNotFoundError):
        return ObservationOutcome.NOT_FOUND
    if isinstance(error, WorkflowUnavailableError):
        return ObservationOutcome.UNAVAILABLE
    return ObservationOutcome.ERROR
