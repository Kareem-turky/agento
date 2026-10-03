"""Governed handlers of the human decisions (Task 036).

Each decision is an ``ActionHandler`` run by ``ExecutionCoordinator`` (governance ->
validate -> execute -> verify -> audit), so it is audited by the existing audit trail
(action, decider, company, status, safe approval reference; never the note or summary).
The decider comes only from the trusted ``ActionExecutionContext``; the repository's
compare-and-set (and the database) refuse a decision on a request that is no longer
pending, and an approval or rejection by the requester.
"""

from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID

from pydantic import BaseModel, ConfigDict, ValidationError

from app.approval_management import actions
from app.approval_management.contracts import ApprovalRepository
from app.approval_management.errors import (
    ApprovalInputError,
    ApprovalRepositoryError,
    ApprovalTransitionConflictError,
)
from app.approval_management.models import DecisionNote, normalize_note
from app.approval_management.state import ApprovalStatus
from app.execution import (
    ActionExecutionContext,
    ExecutionFailedWithoutEffect,
    ExecutionOutcomeUncertain,
    ExecutionResult,
    VerificationResult,
)
from app.execution.errors import ActionInputError

_FROZEN = ConfigDict(frozen=True, extra="forbid")


def _utc_now() -> datetime:
    return datetime.now(UTC)


class _Decision(BaseModel):
    model_config = _FROZEN
    approval_id: UUID
    note: DecisionNote | None = None


class DecideApprovalHandler:
    """approve / reject / cancel: ``requested`` -> the handler's target status."""

    def __init__(self, repository: ApprovalRepository, target: ApprovalStatus,
                 clock: Callable[[], datetime]) -> None:  # fmt: skip
        definition = {
            ApprovalStatus.APPROVED: actions.REQUEST_APPROVE,
            ApprovalStatus.REJECTED: actions.REQUEST_REJECT,
            ApprovalStatus.CANCELLED: actions.REQUEST_CANCEL,
        }[target]
        self.action_name = definition.name
        self._target = target
        self._repository = repository
        self._clock = clock

    def validate(self, parameters: Mapping[str, Any]) -> BaseModel:
        try:
            note = normalize_note(parameters.get("note"),
                                  required=self._target is not ApprovalStatus.APPROVED)  # fmt: skip
            return _Decision(approval_id=parameters.get("approval_id"), note=note)  # type: ignore[arg-type]
        except (ValidationError, ApprovalInputError, TypeError, ValueError):
            raise ActionInputError("invalid approval decision") from None

    async def execute(
        self, context: ActionExecutionContext, validated_input: BaseModel
    ) -> ExecutionResult:
        data = cast(_Decision, validated_input)
        if not context.actor_id or not context.actor_type:
            raise ExecutionFailedWithoutEffect()
        try:
            await self._repository.decide(
                context.company_id, data.approval_id, self._target, context.actor_id,
                context.actor_type, data.note, self._clock(),
            )  # fmt: skip
        except ApprovalTransitionConflictError:
            raise ExecutionFailedWithoutEffect() from None  # nothing was written
        except ApprovalRepositoryError:
            raise ExecutionOutcomeUncertain() from None  # the commit may have happened
        return ExecutionResult(reference_id=str(data.approval_id))

    async def verify(
        self, context: ActionExecutionContext, validated_input: BaseModel,
        execution_result: ExecutionResult | None,
    ) -> VerificationResult:  # fmt: skip
        data = cast(_Decision, validated_input)
        try:
            stored = await self._repository.get(context.company_id, data.approval_id)
        except ApprovalRepositoryError:
            return VerificationResult(verified=False, reason_code="approval_state_unknown")
        if (
            stored is None
            or stored.status is not self._target
            or stored.decided_by_actor_id != context.actor_id
        ):
            return VerificationResult(verified=False, reason_code="approval_not_decided")
        return VerificationResult(verified=True, reason_code=f"approval_{self._target.value}")


def build_approval_handlers(
    repository: ApprovalRepository, *, clock: Callable[[], datetime] = _utc_now
) -> tuple[DecideApprovalHandler, ...]:
    return tuple(DecideApprovalHandler(repository, target, clock)
                 for target in (ApprovalStatus.APPROVED, ApprovalStatus.REJECTED,
                                ApprovalStatus.CANCELLED))  # fmt: skip
