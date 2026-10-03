"""``ApprovalService``: the human side of Product approvals (Task 036).

    reads      trusted RequestContext -> GovernanceGate (approvals.read) -> 403 / data
    decisions  trusted RequestContext -> GovernanceGate (approvals.decide / .cancel)
                 denied (incl. any system_agent)  -> ExecutionCoordinator audits -> 403
                 -> bounded note (reject/cancel: required)                       -> 422
                 -> request of THIS company? (lazy expiry first)                 -> 404
                 -> approver may act for the request's store?                    -> 403
                 -> approve/reject by the requester (two-person rule)            -> 403
                 -> still requested?                                             -> 409
                 -> ExecutionCoordinator -> decision handler (CAS) -> verify -> AUDIT
    resume     the REQUESTER continues the Workflow linked to a granted request
               (explicit continuation only; the governed write re-checks governance
               and consumes the request once)

There is no create operation: requests exist only because governance returned
REQUIRE_APPROVAL for a real governed action (``ProductApprovalBroker``). A decision note
is inert audit text: never executed, interpreted, sent anywhere or given to an Agent.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol, runtime_checkable
from uuid import UUID

from app.approval_management import actions
from app.approval_management.contracts import ApprovalRepository
from app.approval_management.errors import (
    ApprovalAccessDeniedError,
    ApprovalConflictError,
    ApprovalError,
    ApprovalInputError,
    ApprovalInputReason,
    ApprovalNotFoundError,
    ApprovalNotResumableError,
    ApprovalOperationFailedError,
    ApprovalRepositoryError,
    ApprovalSelfDecisionError,
    ApprovalUnavailableError,
)
from app.approval_management.models import ApprovalEvent, ApprovalRequest, normalize_note
from app.approval_management.state import ApprovalStatus
from app.context.models import ActorContext, RequestContext
from app.execution import ActionRunStatus, ApprovalSource, ExecutionCoordinator
from app.governance import (
    ActionDefinition,
    ActionIntent,
    ActionScope,
    GovernanceGate,
    PolicyOutcome,
)
from app.observability.contracts import (
    BusinessDetails,
    ObservationDetails,
    ObservationOutcome,
    ProductObservability,
    ProductOperation,
    observe,
)
from app.workflow_management.errors import (
    WorkflowAccessDeniedError,
    WorkflowInputInvalidError,
    WorkflowLeaseConflictError,
    WorkflowNotResumableError,
    WorkflowRunNotFoundError,
)

MAX_LIST_LIMIT = 100
_ACTION_NAME = r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$"


def _utc_now() -> datetime:
    return datetime.now(UTC)


class ApprovalDecision(StrEnum):
    """Low-cardinality observability label of a human decision."""

    APPROVE = "approve"
    REJECT = "reject"
    CANCEL = "cancel"


@dataclass(frozen=True, slots=True)
class ApprovalDetail:
    request: ApprovalRequest
    events: tuple[ApprovalEvent, ...]


@dataclass(frozen=True, slots=True)
class WorkflowResumeOutcome:
    """Safe result of an approval continuation (identifiers and status codes only)."""

    workflow_run_id: UUID
    workflow_id: str
    status: str
    failure_code: str | None


class _WorkflowRunOutcome(Protocol):
    """The safe fields of a Workflow run result this service reads."""

    @property
    def run_id(self) -> UUID: ...
    @property
    def workflow_id(self) -> str: ...
    @property
    def status(self) -> StrEnum: ...
    @property
    def failure_code(self) -> StrEnum | None: ...


@runtime_checkable
class ApprovalWorkflowResumer(Protocol):
    """The explicit Workflow continuation after a human approval (``WorkflowEngine``)."""

    async def resume_after_approval(
        self, request: RequestContext, run_id: UUID, approval_id: UUID
    ) -> _WorkflowRunOutcome: ...


_DECISIONS: Mapping[ApprovalDecision, tuple[ApprovalStatus, ActionDefinition]] = {
    ApprovalDecision.APPROVE: (ApprovalStatus.APPROVED, actions.REQUEST_APPROVE),
    ApprovalDecision.REJECT: (ApprovalStatus.REJECTED, actions.REQUEST_REJECT),
    ApprovalDecision.CANCEL: (ApprovalStatus.CANCELLED, actions.REQUEST_CANCEL),
}


def _outcome(error: BaseException) -> ObservationOutcome:
    if isinstance(error, ApprovalAccessDeniedError):
        return ObservationOutcome.DENIED
    if isinstance(error, ApprovalInputError):
        return ObservationOutcome.INVALID
    if isinstance(error, ApprovalNotFoundError):
        return ObservationOutcome.NOT_FOUND
    if isinstance(error, (ApprovalConflictError, ApprovalOperationFailedError,
                          ApprovalNotResumableError)):  # fmt: skip
        return ObservationOutcome.CONFLICT
    if isinstance(error, ApprovalUnavailableError):
        return ObservationOutcome.UNAVAILABLE
    return ObservationOutcome.ERROR


def _uuid(value: object) -> UUID:
    """A malformed id is indistinguishable from a missing request."""
    try:
        return value if isinstance(value, UUID) else UUID(str(value))
    except ValueError:
        raise ApprovalNotFoundError() from None


class ApprovalService:
    def __init__(
        self,
        repository: ApprovalRepository,
        gate: GovernanceGate,
        coordinator: ExecutionCoordinator,
        *,
        clock: Callable[[], datetime] = _utc_now,
        observability: ProductObservability | None = None,
        workflows: ApprovalWorkflowResumer | None = None,
    ) -> None:
        self._repository = repository
        self._gate = gate
        self._coordinator = coordinator
        self._clock = clock
        self._observability = observability
        self._workflows = workflows

    # ----- authorization -----------------------------------------------------------------------

    @staticmethod
    def _actor(context: RequestContext) -> ActorContext:
        if context.actor is None:
            raise ApprovalAccessDeniedError()
        return context.actor

    def _allowed(self, actor: ActorContext, action: ActionDefinition) -> bool:
        decision = self._gate.decide(
            actor, ActionIntent(name=action.name), ActionScope(company_id=actor.company_id)
        )
        return decision.outcome is PolicyOutcome.ALLOW

    def _authorize_read(self, context: RequestContext) -> ActorContext:
        actor = self._actor(context)
        if not self._allowed(actor, actions.REQUEST_READ):
            raise ApprovalAccessDeniedError()
        return actor

    async def _authorize_decision(
        self, context: RequestContext, action: ActionDefinition
    ) -> ActorContext:
        actor = self._actor(context)
        if not self._allowed(actor, action):
            # The coordinator records the denial in the audit trail, then stops.
            await self._coordinator.run(
                context, ActionIntent(name=action.name),
                ActionScope(company_id=actor.company_id), {},
            )  # fmt: skip
            raise ApprovalAccessDeniedError()
        return actor

    def _now(self) -> datetime:
        now = self._clock()
        if not isinstance(now, datetime) or now.utcoffset() is None:
            raise ApprovalUnavailableError()
        return now

    async def _current(self, company_id: str, approval_id: UUID) -> ApprovalRequest:
        """The request of THIS company, after lazy expiry (else 404)."""
        try:
            await self._repository.expire_due(company_id, self._now(), approval_id)
            stored = await self._repository.get(company_id, approval_id)
        except ApprovalRepositoryError:
            raise ApprovalUnavailableError() from None
        if stored is None:
            raise ApprovalNotFoundError()
        return stored

    # ----- reads ---------------------------------------------------------------------------------

    async def list_requests(
        self, context: RequestContext, *, status: object = None, action_name: object = None,
        limit: int = 50,
    ) -> tuple[ApprovalRequest, ...]:  # fmt: skip
        actor = self._authorize_read(context)
        try:
            wanted = None if status is None else ApprovalStatus(status)  # type: ignore[arg-type]
        except ValueError:
            raise ApprovalInputError(ApprovalInputReason.FILTER_INVALID) from None
        if action_name is not None and (not isinstance(action_name, str)
                                        or len(action_name) > 128):  # fmt: skip
            raise ApprovalInputError(ApprovalInputReason.FILTER_INVALID)
        if type(limit) is not int or not 1 <= limit <= MAX_LIST_LIMIT:
            raise ApprovalInputError(ApprovalInputReason.FILTER_INVALID)
        try:
            await self._repository.expire_due(actor.company_id, self._now())
            return await self._repository.list(actor.company_id, status=wanted,
                                               action_name=action_name, limit=limit)  # fmt: skip
        except ApprovalRepositoryError:
            raise ApprovalUnavailableError() from None

    async def get_request(self, context: RequestContext, approval_id: object) -> ApprovalDetail:
        actor = self._authorize_read(context)
        current = await self._current(actor.company_id, _uuid(approval_id))
        try:
            events = await self._repository.events(actor.company_id, current.approval_id)
        except ApprovalRepositoryError:
            raise ApprovalUnavailableError() from None
        return ApprovalDetail(current, events)

    # ----- human decisions -----------------------------------------------------------------------

    async def approve(self, context: RequestContext, approval_id: object,
                      note: object = None) -> ApprovalRequest:  # fmt: skip
        return await self._decide(context, ApprovalDecision.APPROVE, approval_id, note)

    async def reject(self, context: RequestContext, approval_id: object,
                     note: object) -> ApprovalRequest:  # fmt: skip
        return await self._decide(context, ApprovalDecision.REJECT, approval_id, note)

    async def cancel(self, context: RequestContext, approval_id: object,
                     note: object) -> ApprovalRequest:  # fmt: skip
        return await self._decide(context, ApprovalDecision.CANCEL, approval_id, note)

    async def _decide(self, context: RequestContext, decision: ApprovalDecision,
                      approval_id: object, note: object) -> ApprovalRequest:  # fmt: skip
        target, action = _DECISIONS[decision]
        with observe(self._observability, ProductOperation.APPROVAL_DECISION,
                     context.request_id) as obs:  # fmt: skip
            try:
                actor = await self._authorize_decision(context, action)
                text = normalize_note(note, required=target is not ApprovalStatus.APPROVED)
                current = await self._current(actor.company_id, _uuid(approval_id))
                if current.store_id is not None and current.store_id not in actor.store_ids:
                    raise ApprovalAccessDeniedError()  # not an approver for that store
                if target is not ApprovalStatus.CANCELLED and (
                    current.requester_actor_id == actor.actor_id
                ):
                    raise ApprovalSelfDecisionError()  # two-person rule
                if current.status is not ApprovalStatus.REQUESTED:
                    raise ApprovalConflictError()
                run = await self._coordinator.run(
                    context, ActionIntent(name=action.name),
                    ActionScope(company_id=actor.company_id, store_id=current.store_id),
                    {"approval_id": str(current.approval_id), "note": text},
                )  # fmt: skip
                if run.status is ActionRunStatus.DENIED:
                    raise ApprovalAccessDeniedError()
                after = await self._current(actor.company_id, current.approval_id)
                if run.status is not ActionRunStatus.VERIFIED:
                    if after.status is not ApprovalStatus.REQUESTED:
                        raise ApprovalConflictError()  # another decision (or expiry) won
                    raise ApprovalOperationFailedError()
            except ApprovalError as error:
                obs.finish(_outcome(error), ObservationDetails(
                    business=BusinessDetails(status=decision)))  # fmt: skip
                raise
            obs.finish(ObservationOutcome.COMPLETED, ObservationDetails(
                business=BusinessDetails(status=decision, reason=after.risk)))  # fmt: skip
            return after

    # ----- explicit Workflow continuation --------------------------------------------------------

    async def resume_workflow(
        self, context: RequestContext, approval_id: object
    ) -> WorkflowResumeOutcome:
        actor = self._authorize_read(context)
        current = await self._current(actor.company_id, _uuid(approval_id))
        source = current.source
        if source.kind is not ApprovalSource.WORKFLOW_STEP or source.workflow_run_id is None:
            raise ApprovalNotResumableError()
        if (actor.actor_id, actor.actor_type) != (current.requester_actor_id,
                                                  current.requester_actor_type):  # fmt: skip
            raise ApprovalAccessDeniedError()  # only the requester continues their Workflow
        if (current.status is not ApprovalStatus.APPROVED or current.consumed
                or current.expires_at <= self._now()):  # fmt: skip
            raise ApprovalNotResumableError()
        if self._workflows is None:
            raise ApprovalUnavailableError()
        try:
            result = await self._workflows.resume_after_approval(
                context, source.workflow_run_id, current.approval_id
            )
        except ApprovalError:
            raise
        except Exception as error:  # noqa: BLE001 - mapped to fixed, value-free answers
            raise _resume_error(error) from None
        return WorkflowResumeOutcome(
            workflow_run_id=result.run_id,
            workflow_id=result.workflow_id,
            status=result.status.value,
            failure_code=None if result.failure_code is None else result.failure_code.value,
        )


def _resume_error(error: Exception) -> ApprovalError:
    if isinstance(error, (WorkflowRunNotFoundError, WorkflowNotResumableError,
                          WorkflowAccessDeniedError, WorkflowInputInvalidError)):  # fmt: skip
        return ApprovalNotResumableError()
    if isinstance(error, WorkflowLeaseConflictError):
        return ApprovalConflictError()
    return ApprovalUnavailableError()
