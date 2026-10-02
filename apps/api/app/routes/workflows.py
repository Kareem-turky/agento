"""Product Workflow inspection API (Task 034): READ-ONLY views of the immutable Workflow
catalog and of this company's durable Workflow run history.

    GET /api/v1/workflows/catalog                    workflows.read
    GET /api/v1/workflows/workflow?workflow_id=      workflows.read
    GET /api/v1/workflows/runs?limit=                workflows.read
    GET /api/v1/workflows/run?run_id=                workflows.read

There is deliberately NO run, resume, retry, create, update or delete endpoint: Workflows
are executed only by trusted Product services (for example
``GET /api/v1/operations/reports/daily``) under their own business permissions. Paths
are FIXED (ids are query parameters) so the AgentOS authentication exemption stays a list
of exact paths. Product authentication only; never AgentOS.

Responses expose identifiers, versions, statuses, Step ids, attempt metadata, safe failure
codes and timestamps only: never the run input, a checkpoint, a Step output (such as the
report), an actor or store identifier, the execution claim, a provider value, model
output or exception text. Nothing here calls a model, a tool, a provider or the network.
"""

from collections.abc import Callable, Coroutine
from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field

from app.context import CurrentActor, CurrentRequestContext
from app.routes.integrations import SafeValidationRoute
from app.workflow_management import (
    CheckpointPolicy,
    StepAttemptStatus,
    StepSideEffect,
    VerificationCode,
    WorkflowCategory,
    WorkflowDefinition,
    WorkflowEventType,
    WorkflowFailureCode,
    WorkflowInputKind,
    WorkflowLifecycle,
    WorkflowRunStatus,
)
from app.workflow_management.errors import (
    WorkflowAccessForbiddenError,
    WorkflowError,
    WorkflowNotFoundError,
    WorkflowRunNotFoundError,
)
from app.workflow_management.records import (
    StepAttemptRecord,
    WorkflowEventRecord,
    WorkflowRunRecord,
)
from app.workflow_management.service import MAX_RUNS_LIMIT, WorkflowInspectionService

WORKFLOWS_SERVICE_STATE_KEY = "workflow_inspection_service"
WORKFLOWS_CATALOG_PATH = "/api/v1/workflows/catalog"
WORKFLOW_PATH = "/api/v1/workflows/workflow"
WORKFLOW_RUNS_PATH = "/api/v1/workflows/runs"
WORKFLOW_RUN_PATH = "/api/v1/workflows/run"
WORKFLOWS_PATHS = (WORKFLOWS_CATALOG_PATH, WORKFLOW_PATH, WORKFLOW_RUNS_PATH, WORKFLOW_RUN_PATH)

router = APIRouter(tags=["workflows"], route_class=SafeValidationRoute)
_FROZEN = ConfigDict(frozen=True, extra="forbid")

# ----- models -----------------------------------------------------------------------------------


class WorkflowInputFieldView(BaseModel):
    model_config = _FROZEN
    name: str
    label: str
    kind: WorkflowInputKind
    required: bool
    description: str


class WorkflowStepView(BaseModel):
    model_config = _FROZEN
    step_id: str
    name: str
    description: str
    handler_id: str = Field(description="Stable Product handler id (bound to trusted code by "
                                        "the deployment; never an import path).")  # fmt: skip
    side_effect: StepSideEffect
    timeout_seconds: int
    max_attempts: int = Field(description="Bounded attempts; governed writes run once.")
    checkpoint_policy: CheckpointPolicy


class WorkflowDefinitionView(BaseModel):
    model_config = _FROZEN
    workflow_id: str
    name: str
    description: str
    category: WorkflowCategory
    version: int
    lifecycle: WorkflowLifecycle
    inputs: list[WorkflowInputFieldView]
    steps: list[WorkflowStepView] = Field(description="Executed strictly in this order.")

    @classmethod
    def of(cls, definition: WorkflowDefinition) -> "WorkflowDefinitionView":
        return cls(
            workflow_id=definition.workflow_id, name=definition.name,
            description=definition.description, category=definition.category,
            version=definition.version, lifecycle=definition.lifecycle,
            inputs=[WorkflowInputFieldView(**f.model_dump()) for f in definition.inputs],
            steps=[WorkflowStepView(**s.model_dump()) for s in definition.steps],
        )  # fmt: skip


class WorkflowRunView(BaseModel):
    model_config = _FROZEN
    run_id: UUID
    workflow_id: str
    workflow_version: int
    request_id: UUID
    status: WorkflowRunStatus
    current_step_id: str | None
    failure_code: WorkflowFailureCode | None
    attempt_count: int
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None

    @classmethod
    def of(cls, run: WorkflowRunRecord, attempt_count: int) -> "WorkflowRunView":
        return cls(
            run_id=run.run_id, workflow_id=run.workflow_id,
            workflow_version=run.workflow_version, request_id=run.request_id,
            status=run.status, current_step_id=run.current_step_id,
            failure_code=run.failure_code, attempt_count=attempt_count,
            created_at=run.created_at, updated_at=run.updated_at, completed_at=run.completed_at,
        )  # fmt: skip


class StepAttemptView(BaseModel):
    model_config = _FROZEN
    step_id: str
    attempt: int
    handler_id: str
    status: StepAttemptStatus
    failure_code: WorkflowFailureCode | None
    verification_code: VerificationCode | None
    started_at: datetime
    completed_at: datetime | None

    @classmethod
    def of(cls, attempt: StepAttemptRecord) -> "StepAttemptView":
        return cls(
            step_id=attempt.step_id, attempt=attempt.attempt, handler_id=attempt.handler_id,
            status=attempt.status, failure_code=attempt.failure_code,
            verification_code=attempt.verification_code, started_at=attempt.started_at,
            completed_at=attempt.completed_at,
        )  # fmt: skip


class WorkflowEventView(BaseModel):
    model_config = _FROZEN
    sequence: int
    event_type: WorkflowEventType
    step_id: str | None
    attempt: int | None
    status: str | None
    failure_code: WorkflowFailureCode | None
    occurred_at: datetime

    @classmethod
    def of(cls, event: WorkflowEventRecord) -> "WorkflowEventView":
        return cls(
            sequence=event.sequence, event_type=event.event_type, step_id=event.step_id,
            attempt=event.attempt, status=event.status, failure_code=event.failure_code,
            occurred_at=event.occurred_at,
        )  # fmt: skip


class WorkflowCatalogResponse(BaseModel):
    model_config = _FROZEN
    request_id: UUID
    workflows: list[WorkflowDefinitionView]


class WorkflowResponse(BaseModel):
    model_config = _FROZEN
    request_id: UUID
    workflow: WorkflowDefinitionView


class WorkflowRunListResponse(BaseModel):
    model_config = _FROZEN
    request_id: UUID
    runs: list[WorkflowRunView] = Field(description="This company's runs, newest first.")


class WorkflowRunResponse(BaseModel):
    model_config = _FROZEN
    request_id: UUID
    run: WorkflowRunView
    attempts: list[StepAttemptView] = Field(
        description="Every Step attempt (a retry is a new attempt; none is overwritten)."
    )
    events: list[WorkflowEventView] = Field(
        description="Append-only lifecycle events (safe metadata; not the action audit)."
    )


# ----- plumbing ---------------------------------------------------------------------------------


def _service(request: Request) -> WorkflowInspectionService:
    service = getattr(request.app.state, WORKFLOWS_SERVICE_STATE_KEY, None)
    if not isinstance(service, WorkflowInspectionService):
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail="Workflows unavailable")
    return service


def _http(error: Exception) -> HTTPException:
    if isinstance(error, WorkflowAccessForbiddenError):
        return HTTPException(status.HTTP_403_FORBIDDEN, detail="Forbidden")
    if isinstance(error, WorkflowNotFoundError | WorkflowRunNotFoundError):
        return HTTPException(status.HTTP_404_NOT_FOUND, detail=error.message)
    if isinstance(error, WorkflowError):
        return HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail="Workflows unavailable")
    return HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail="Workflows unavailable")


async def _call[T](call: Coroutine[Any, Any, T]) -> T:
    try:
        return await call
    except Exception as error:  # noqa: BLE001 - mapped to fixed, value-free answers
        raise _http(error) from None


def _sync[T](call: Callable[[], T]) -> T:
    try:
        return call()
    except Exception as error:  # noqa: BLE001 - mapped to fixed, value-free answers
        raise _http(error) from None


_ID_PATTERN = r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$"
WorkflowIdQuery = Annotated[
    str, Query(description="A Product Workflow id.", max_length=128, pattern=_ID_PATTERN)
]
RunIdQuery = Annotated[UUID, Query(description="A Workflow run id of your company.")]
LimitQuery = Annotated[int, Query(ge=1, le=MAX_RUNS_LIMIT, description="At most this many runs.")]
_ERRORS: dict[int | str, dict[str, Any]] = {
    401: {"description": "Missing or invalid Product API key."},
    403: {"description": "The actor lacks `workflows.read`."},
    503: {"description": "Workflow inspection unavailable."},
}
_ONE: dict[int | str, dict[str, Any]] = {
    **_ERRORS,
    404: {"description": "No such Workflow / no such run in your company (indistinguishable)."},
    422: {"description": "Invalid id (submitted values are never echoed)."},
}
_READ_ONLY = ("Read-only: there is no Workflow run, resume or retry endpoint; Workflows run "
              "only inside trusted Product services.")  # fmt: skip


def _docs(text: str) -> str:
    return f"{text} {_READ_ONLY}\n\nRequires the `workflows.read` Product permission."


# ----- routes -----------------------------------------------------------------------------------


@router.get(WORKFLOWS_CATALOG_PATH, response_model=WorkflowCatalogResponse, responses=_ERRORS,
            summary="List Product Workflows",
            description=_docs("The immutable Product Workflows of this build: ordered Steps, "
                              "side-effect class, timeouts and bounded attempts."))  # fmt: skip
async def get_workflow_catalog(
    context: CurrentRequestContext, actor: CurrentActor, request: Request
) -> WorkflowCatalogResponse:
    service = _service(request)
    definitions = _sync(lambda: service.catalog(context))
    return WorkflowCatalogResponse(
        request_id=context.request_id, workflows=[WorkflowDefinitionView.of(d) for d in definitions]
    )


@router.get(WORKFLOW_PATH, response_model=WorkflowResponse, responses=_ONE,
            summary="Get one Product Workflow",
            description=_docs("One Product Workflow definition."))  # fmt: skip
async def get_workflow(
    workflow_id: WorkflowIdQuery, context: CurrentRequestContext, actor: CurrentActor,
    request: Request,
) -> WorkflowResponse:  # fmt: skip
    service = _service(request)
    definition = _sync(lambda: service.get_workflow(context, workflow_id))
    return WorkflowResponse(request_id=context.request_id,
                            workflow=WorkflowDefinitionView.of(definition))  # fmt: skip


@router.get(WORKFLOW_RUNS_PATH, response_model=WorkflowRunListResponse, responses=_ERRORS,
            summary="List recent Workflow runs",
            description=_docs("Your company's most recent Workflow runs (status, current "
                              "Step, safe failure code, attempt count, timestamps)."))  # fmt: skip
async def list_workflow_runs(
    context: CurrentRequestContext, actor: CurrentActor, request: Request,
    limit: LimitQuery = 25,
) -> WorkflowRunListResponse:  # fmt: skip
    runs = await _call(_service(request).list_runs(context, limit))
    return WorkflowRunListResponse(request_id=context.request_id,
                                   runs=[WorkflowRunView.of(r, n) for r, n in runs])  # fmt: skip


@router.get(WORKFLOW_RUN_PATH, response_model=WorkflowRunResponse, responses=_ONE,
            summary="Get one Workflow run",
            description=_docs("One run of your company with every Step attempt and its "
                              "append-only lifecycle events. Never the input, a checkpoint "
                              "or a Step output."))  # fmt: skip
async def get_workflow_run(
    run_id: RunIdQuery, context: CurrentRequestContext, actor: CurrentActor, request: Request
) -> WorkflowRunResponse:
    detail = await _call(_service(request).get_run(context, run_id))
    return WorkflowRunResponse(
        request_id=context.request_id,
        run=WorkflowRunView.of(detail.run, len(detail.attempts)),
        attempts=[StepAttemptView.of(a) for a in detail.attempts],
        events=[WorkflowEventView.of(e) for e in detail.events],
    )
