"""Workflow execution vocabularies and explicit state machines (Task 034).

Workflow run::

    pending ──> running ──> succeeded | failed | requires_human | awaiting_approval
       └──────────────────> failed             (stopped before any Step ran)
    running ──> running     (progress, or a recovered expired execution claim)

Step attempt::

    running ──> succeeded | failed | timed_out | requires_human | awaiting_approval

Terminal states never change, with ONE explicit exception (Task 036): a run stopped
``awaiting_approval`` at a governed write Step may go back to ``running`` only through
the explicit approval continuation (``WorkflowEngine.resume_after_approval``, a
repository compare-and-set that also appends ``workflow_approval_resumed``), never
through the generic transitions below and never automatically. A retry is a NEW attempt
row: an attempt's history is never overwritten. Every transition not listed is rejected.
"""

from enum import StrEnum


class WorkflowRunStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    REQUIRES_HUMAN = "requires_human"
    AWAITING_APPROVAL = "awaiting_approval"


class StepAttemptStatus(StrEnum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    REQUIRES_HUMAN = "requires_human"
    AWAITING_APPROVAL = "awaiting_approval"


class WorkflowFailureCode(StrEnum):
    """Stable, safe failure codes. Never an exception message or provider text."""

    INPUT_INVALID = "input_invalid"
    HANDLER_NOT_REGISTERED = "handler_not_registered"
    ACCESS_DENIED = "access_denied"
    STEP_TIMEOUT = "step_timeout"
    STEP_EXECUTION_FAILED = "step_execution_failed"
    STEP_VERIFICATION_FAILED = "step_verification_failed"
    STEP_OUTCOME_UNCERTAIN = "step_outcome_uncertain"
    CHECKPOINT_INVALID = "checkpoint_invalid"
    RETRY_EXHAUSTED = "retry_exhausted"
    EXECUTOR_LOST = "executor_lost"
    APPROVAL_REQUIRED = "approval_required"
    WORKFLOW_UNAVAILABLE = "workflow_unavailable"
    LEASE_CONFLICT = "lease_conflict"


class WorkflowEventType(StrEnum):
    WORKFLOW_REQUESTED = "workflow_requested"
    WORKFLOW_STARTED = "workflow_started"
    WORKFLOW_RESUMED = "workflow_resumed"
    STEP_STARTED = "step_started"
    STEP_SUCCEEDED = "step_succeeded"
    STEP_FAILED = "step_failed"
    STEP_TIMED_OUT = "step_timed_out"
    STEP_RETRYING = "step_retrying"
    WORKFLOW_SUCCEEDED = "workflow_succeeded"
    WORKFLOW_FAILED = "workflow_failed"
    WORKFLOW_REQUIRES_HUMAN = "workflow_requires_human"
    WORKFLOW_AWAITING_APPROVAL = "workflow_awaiting_approval"
    # Task 036: the explicit continuation of a run after a human approval (not a retry).
    WORKFLOW_APPROVAL_RESUMED = "workflow_approval_resumed"


class VerificationCode(StrEnum):
    VERIFIED = "verified"
    NOT_VERIFIED = "not_verified"


class InvalidTransitionError(ValueError):
    """A state change outside the explicit state machine (never applied)."""


R = WorkflowRunStatus
S = StepAttemptStatus

TERMINAL_RUN_STATUSES = frozenset({R.SUCCEEDED, R.FAILED, R.REQUIRES_HUMAN, R.AWAITING_APPROVAL})
ACTIVE_RUN_STATUSES = frozenset({R.PENDING, R.RUNNING})
TERMINAL_STEP_STATUSES = frozenset(set(StepAttemptStatus) - {S.RUNNING})

_RUN_TRANSITIONS: dict[WorkflowRunStatus, frozenset[WorkflowRunStatus]] = {
    R.PENDING: frozenset({R.RUNNING, R.FAILED}),
    R.RUNNING: frozenset({R.RUNNING, *TERMINAL_RUN_STATUSES}),
    **{status: frozenset() for status in TERMINAL_RUN_STATUSES},
}
_STEP_TRANSITIONS: dict[StepAttemptStatus, frozenset[StepAttemptStatus]] = {
    S.RUNNING: TERMINAL_STEP_STATUSES,
    **{status: frozenset() for status in TERMINAL_STEP_STATUSES},
}

# The run status a terminal attempt leads to when the run stops at that attempt.
RUN_STATUS_FOR_STOPPED_STEP: dict[StepAttemptStatus, WorkflowRunStatus] = {
    S.FAILED: R.FAILED,
    S.TIMED_OUT: R.FAILED,
    S.REQUIRES_HUMAN: R.REQUIRES_HUMAN,
    S.AWAITING_APPROVAL: R.AWAITING_APPROVAL,
}
TERMINAL_RUN_EVENT: dict[WorkflowRunStatus, WorkflowEventType] = {
    R.SUCCEEDED: WorkflowEventType.WORKFLOW_SUCCEEDED,
    R.FAILED: WorkflowEventType.WORKFLOW_FAILED,
    R.REQUIRES_HUMAN: WorkflowEventType.WORKFLOW_REQUIRES_HUMAN,
    R.AWAITING_APPROVAL: WorkflowEventType.WORKFLOW_AWAITING_APPROVAL,
}


def check_run_transition(current: WorkflowRunStatus, new: WorkflowRunStatus) -> None:
    if new not in _RUN_TRANSITIONS[WorkflowRunStatus(current)]:
        raise InvalidTransitionError("invalid workflow run transition")


def check_step_transition(current: StepAttemptStatus, new: StepAttemptStatus) -> None:
    if new not in _STEP_TRANSITIONS[StepAttemptStatus(current)]:
        raise InvalidTransitionError("invalid step attempt transition")
