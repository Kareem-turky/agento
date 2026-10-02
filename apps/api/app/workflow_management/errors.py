"""Workflow Platform errors: fixed messages, never a submitted, stored or exception value."""

from app.workflow_management.state import WorkflowFailureCode


class WorkflowError(Exception):
    message = "workflow failed"
    code = WorkflowFailureCode.WORKFLOW_UNAVAILABLE

    def __init__(self) -> None:
        super().__init__(self.message)


class WorkflowUnavailableError(WorkflowError):
    """Not registered in this deployment, or durable state is not guaranteed."""

    message = "Workflow unavailable"


class WorkflowInputInvalidError(WorkflowError):
    message = "Workflow input invalid"
    code = WorkflowFailureCode.INPUT_INVALID


class WorkflowAccessDeniedError(WorkflowError):
    """No trusted actor, or a scope outside the actor's company (nothing was created)."""

    message = "Forbidden"
    code = WorkflowFailureCode.ACCESS_DENIED


class WorkflowLeaseConflictError(WorkflowError):
    """Another executor holds the run, or this executor's claim was taken over."""

    message = "Workflow run is being executed elsewhere"
    code = WorkflowFailureCode.LEASE_CONFLICT


class WorkflowNotFoundError(WorkflowError):
    message = "Workflow not found"


class WorkflowRunNotFoundError(WorkflowError):
    message = "Workflow run not found"


class WorkflowNotResumableError(WorkflowError):
    """Terminal (including awaiting approval: v1 has no approval continuation), or its
    definition version is no longer installed."""

    message = "Workflow run cannot be resumed"


class WorkflowAccessForbiddenError(WorkflowError):
    """The reader lacks ``workflows.read``."""

    message = "Forbidden"
    code = WorkflowFailureCode.ACCESS_DENIED
