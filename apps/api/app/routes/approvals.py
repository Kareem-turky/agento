"""Product human-approval API (Task 036).

    GET  /api/v1/approvals?status=&action_name=&limit=           approvals.read
    GET  /api/v1/approvals/approval?approval_id=                  approvals.read
    POST /api/v1/approvals/approval/approve?approval_id=          approvals.decide
    POST /api/v1/approvals/approval/reject?approval_id=           approvals.decide
    POST /api/v1/approvals/approval/cancel?approval_id=           approvals.cancel
    POST /api/v1/approvals/approval/resume-workflow?approval_id=  approvals.read (requester)

There is deliberately NO create endpoint: a request exists only because governance
returned REQUIRE_APPROVAL for a real governed action, so no client, user or model can
choose an action, risk, requester, company, store or fingerprint. Paths are FIXED (ids
are query parameters) so the AgentOS exemption stays a list of exact paths. Product
authentication only; never AgentOS. Another company's request is indistinguishable from
a missing one (404). Responses expose the safe summary and decision metadata only: never
the subject fingerprint, raw parameters, an idempotency key hash or a provider payload.
Decision notes are inert text. Error answers never echo submitted values.
"""

from collections.abc import Coroutine
from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field, StrictStr

from app.approval_management.errors import (
    ApprovalAccessDeniedError,
    ApprovalConflictError,
    ApprovalError,
    ApprovalInputError,
    ApprovalNotFoundError,
    ApprovalNotResumableError,
    ApprovalOperationFailedError,
    ApprovalSelfDecisionError,
)
from app.approval_management.models import MAX_NOTE_CHARS, ApprovalEvent, ApprovalRequest
from app.approval_management.service import MAX_LIST_LIMIT, ApprovalService
from app.approval_management.state import ApprovalEventType, ApprovalStatus
from app.context import CurrentActor, CurrentRequestContext
from app.routes.integrations import SafeValidationRoute

APPROVALS_SERVICE_STATE_KEY = "approval_service"
APPROVALS_PATH = "/api/v1/approvals"
APPROVAL_PATH = "/api/v1/approvals/approval"
APPROVE_PATH = "/api/v1/approvals/approval/approve"
REJECT_PATH = "/api/v1/approvals/approval/reject"
CANCEL_PATH = "/api/v1/approvals/approval/cancel"
RESUME_WORKFLOW_PATH = "/api/v1/approvals/approval/resume-workflow"
APPROVALS_PATHS = (APPROVALS_PATH, APPROVAL_PATH, APPROVE_PATH, REJECT_PATH, CANCEL_PATH,
                   RESUME_WORKFLOW_PATH)  # fmt: skip

router = APIRouter(tags=["approvals"], route_class=SafeValidationRoute)
_FROZEN = ConfigDict(frozen=True, extra="forbid")
SELF_DECISION = "Requesters cannot decide their own request"

# ----- models -----------------------------------------------------------------------------------


class ChangeView(BaseModel):
    model_config = _FROZEN
    code: str
    label: str
    before: str | None
    after: str | None


class SummaryView(BaseModel):
    model_config = _FROZEN
    title: str
    description: str
    changes: list[ChangeView]


class SourceView(BaseModel):
    model_config = _FROZEN
    kind: str = Field(description="action | write_command | workflow_step")
    command_id: UUID | None
    workflow_run_id: UUID | None
    workflow_id: str | None
    workflow_step_id: str | None


class ApprovalView(BaseModel):
    model_config = _FROZEN
    approval_id: UUID
    action_name: str
    risk: str
    status: ApprovalStatus
    requester_actor_id: str
    requester_actor_type: str
    store_id: str | None
    created_at: datetime
    expires_at: datetime
    decided_at: datetime | None
    decided_by_actor_id: str | None
    decided_by_actor_type: str | None
    decision_note: str | None = Field(description="Inert human text (never instructions).")
    summary: SummaryView = Field(description="Trusted display only; never what is approved.")
    source: SourceView
    action_run_id: UUID = Field(description="The run that requested the decision.")
    consumed: bool
    consumed_by_action_run_id: UUID | None
    execution_outcome: str | None

    @classmethod
    def of(cls, approval: ApprovalRequest) -> "ApprovalView":
        s, src = approval.summary, approval.source
        return cls(
            approval_id=approval.approval_id, action_name=approval.action_name,
            risk=approval.risk.value, status=approval.status,
            requester_actor_id=approval.requester_actor_id,
            requester_actor_type=approval.requester_actor_type, store_id=approval.store_id,
            created_at=approval.created_at, expires_at=approval.expires_at,
            decided_at=approval.decided_at, decided_by_actor_id=approval.decided_by_actor_id,
            decided_by_actor_type=approval.decided_by_actor_type,
            decision_note=approval.decision_note,
            summary=SummaryView(title=s.title, description=s.description,
                                changes=[ChangeView(**c.model_dump()) for c in s.changes]),
            source=SourceView(kind=src.kind.value, command_id=src.command_id,
                              workflow_run_id=src.workflow_run_id, workflow_id=src.workflow_id,
                              workflow_step_id=src.workflow_step_id),
            action_run_id=approval.action_run_id, consumed=approval.consumed,
            consumed_by_action_run_id=approval.consumed_by_action_run_id,
            execution_outcome=None if approval.execution_outcome is None
            else approval.execution_outcome.value,
        )  # fmt: skip


class EventView(BaseModel):
    model_config = _FROZEN
    sequence: int
    event_type: ApprovalEventType
    status: ApprovalStatus
    actor_id: str | None
    actor_type: str | None
    action_run_id: UUID | None
    execution_outcome: str | None
    occurred_at: datetime

    @classmethod
    def of(cls, event: ApprovalEvent) -> "EventView":
        return cls(sequence=event.sequence, event_type=event.event_type, status=event.status,
                   actor_id=event.actor_id, actor_type=event.actor_type,
                   action_run_id=event.action_run_id,
                   execution_outcome=None if event.execution_outcome is None
                   else event.execution_outcome.value, occurred_at=event.occurred_at)  # fmt: skip


class ApprovalListResponse(BaseModel):
    model_config = _FROZEN
    request_id: UUID
    approvals: list[ApprovalView] = Field(description="Newest first.")


class ApprovalResponse(BaseModel):
    model_config = _FROZEN
    request_id: UUID
    approval: ApprovalView
    events: list[EventView] = Field(description="Append-only lifecycle history.")


class WorkflowResumeResponse(BaseModel):
    model_config = _FROZEN
    request_id: UUID
    workflow_run_id: UUID
    workflow_id: str
    status: str
    failure_code: str | None


class DecisionRequest(BaseModel):
    model_config = _FROZEN
    note: StrictStr | None = Field(
        default=None,
        max_length=MAX_NOTE_CHARS * 2,
        description="Approve: optional. Reject/cancel: required.",
    )


# ----- plumbing ---------------------------------------------------------------------------------


def _service(request: Request) -> ApprovalService:
    service = getattr(request.app.state, APPROVALS_SERVICE_STATE_KEY, None)
    if not isinstance(service, ApprovalService):
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail="Approvals unavailable")
    return service


def _http(error: Exception) -> HTTPException:
    if isinstance(error, ApprovalSelfDecisionError):
        return HTTPException(status.HTTP_403_FORBIDDEN, detail=SELF_DECISION)
    if isinstance(error, ApprovalAccessDeniedError):
        return HTTPException(status.HTTP_403_FORBIDDEN, detail="Forbidden")
    if isinstance(error, ApprovalNotFoundError):
        return HTTPException(status.HTTP_404_NOT_FOUND, detail=error.message)
    if isinstance(error, ApprovalInputError):
        return HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"message": error.message, "code": error.reason.value},
        )
    if isinstance(error, (ApprovalConflictError, ApprovalNotResumableError,
                          ApprovalOperationFailedError)):  # fmt: skip
        return HTTPException(status.HTTP_409_CONFLICT, detail=error.message)
    if isinstance(error, ApprovalError):
        return HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail="Approvals unavailable")
    return HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail="Approvals unavailable")


async def _call[T](call: Coroutine[Any, Any, T]) -> T:
    try:
        return await call
    except Exception as error:  # noqa: BLE001 - mapped to fixed, value-free answers
        raise _http(error) from None


ApprovalIdQuery = Annotated[UUID, Query(description="An approval request id of your company.")]
StatusQuery = Annotated[ApprovalStatus | None, Query(description="Only this status.")]
ActionQuery = Annotated[str | None, Query(max_length=128, pattern=r"^[a-z][a-z0-9_.]{0,127}$",
                                          description="Only this action.")]  # fmt: skip
LimitQuery = Annotated[int, Query(ge=1, le=MAX_LIST_LIMIT, description="At most this many.")]
_ERRORS: dict[int | str, dict[str, Any]] = {
    401: {"description": "Missing or invalid Product API key."},
    403: {"description": "The actor lacks the required approvals permission."},
    503: {"description": "Approvals unavailable."},
}
_ONE: dict[int | str, dict[str, Any]] = {
    **_ERRORS,
    404: {"description": "No such request in your company (indistinguishable)."},
    422: {"description": "Invalid input (stable `code`; submitted values are never echoed)."},
}
_DECIDE: dict[int | str, dict[str, Any]] = {
    **_ONE,
    409: {
        "description": "The request is no longer pending (decided, expired or a race lost)."
    },  # fmt: skip
}
_INTERNAL_ONLY = ("Requests are created ONLY by Product governance (REQUIRE_APPROVAL); there "
                  "is no create endpoint. An approval never replaces a permission.")  # fmt: skip


def _docs(permission: str, text: str) -> str:
    return f"{text} {_INTERNAL_ONLY}\n\nRequires the `{permission}` Product permission."


# ----- reads ------------------------------------------------------------------------------------


@router.get(APPROVALS_PATH, response_model=ApprovalListResponse, responses=_ONE,
            summary="List human approval requests",
            description=_docs("approvals.read", "Your company's requests, newest first "
                              "(expired requests are marked on access)."))  # fmt: skip
async def list_approvals(
    context: CurrentRequestContext, actor: CurrentActor, request: Request,
    status_filter: Annotated[StatusQuery, Query(alias="status")] = None,
    action_name: ActionQuery = None, limit: LimitQuery = 50,
) -> ApprovalListResponse:  # fmt: skip
    approvals = await _call(_service(request).list_requests(
        context, status=status_filter, action_name=action_name, limit=limit))  # fmt: skip
    return ApprovalListResponse(request_id=context.request_id,
                                approvals=[ApprovalView.of(a) for a in approvals])  # fmt: skip


@router.get(APPROVAL_PATH, response_model=ApprovalResponse, responses=_ONE,
            summary="Get one human approval request",
            description=_docs("approvals.read", "The request, its safe summary and its "
                              "append-only history."))  # fmt: skip
async def get_approval(
    approval_id: ApprovalIdQuery, context: CurrentRequestContext, actor: CurrentActor,
    request: Request,
) -> ApprovalResponse:  # fmt: skip
    detail = await _call(_service(request).get_request(context, approval_id))
    return ApprovalResponse(request_id=context.request_id,
                            approval=ApprovalView.of(detail.request),
                            events=[EventView.of(e) for e in detail.events])  # fmt: skip


# ----- human decisions --------------------------------------------------------------------------


async def _decided(context: Any, request: Request, approval_id: UUID, call: Any) -> Any:
    approval = await _call(call)
    detail = await _call(_service(request).get_request(context, approval_id))
    return ApprovalResponse(request_id=context.request_id, approval=ApprovalView.of(approval),
                            events=[EventView.of(e) for e in detail.events])  # fmt: skip


@router.post(APPROVE_PATH, response_model=ApprovalResponse, responses=_DECIDE,
             summary="Approve a pending request",
             description=_docs("approvals.decide", "Another human than the requester grants "
                               "ONE execution of exactly the requested action (governed and "
                               "audited). An Agent actor is always refused."))  # fmt: skip
async def approve(
    approval_id: ApprovalIdQuery, context: CurrentRequestContext, actor: CurrentActor,
    request: Request, body: DecisionRequest | None = None,
) -> ApprovalResponse:  # fmt: skip
    note = body.note if body else None
    return await _decided(context, request, approval_id,
                          _service(request).approve(context, approval_id, note))  # fmt: skip


@router.post(REJECT_PATH, response_model=ApprovalResponse, responses=_DECIDE,
             summary="Reject a pending request",
             description=_docs("approvals.decide", "Requires a reason. Final: a rejected "
                               "request never becomes approved."))  # fmt: skip
async def reject(
    approval_id: ApprovalIdQuery, body: DecisionRequest, context: CurrentRequestContext,
    actor: CurrentActor, request: Request,
) -> ApprovalResponse:  # fmt: skip
    return await _decided(context, request, approval_id,
                          _service(request).reject(context, approval_id, body.note))  # fmt: skip


@router.post(CANCEL_PATH, response_model=ApprovalResponse, responses=_DECIDE,
             summary="Cancel a pending request",
             description=_docs("approvals.cancel",
                               "Requires a reason (audited). Final."))  # fmt: skip
async def cancel(
    approval_id: ApprovalIdQuery, body: DecisionRequest, context: CurrentRequestContext,
    actor: CurrentActor, request: Request,
) -> ApprovalResponse:  # fmt: skip
    return await _decided(context, request, approval_id,
                          _service(request).cancel(context, approval_id, body.note))  # fmt: skip


@router.post(RESUME_WORKFLOW_PATH, response_model=WorkflowResumeResponse, responses=_DECIDE,
             summary="Continue the Workflow linked to a granted request",
             description=_docs("approvals.read", "Only the requester, only for an approved, "
                               "unconsumed, unexpired request linked to a Workflow Step. The "
                               "governed write re-checks permission and runs at most "
                               "once."))  # fmt: skip
async def resume_workflow(
    approval_id: ApprovalIdQuery, context: CurrentRequestContext, actor: CurrentActor,
    request: Request,
) -> WorkflowResumeResponse:  # fmt: skip
    outcome = await _call(_service(request).resume_workflow(context, approval_id))
    return WorkflowResumeResponse(request_id=context.request_id,
                                  workflow_run_id=outcome.workflow_run_id,
                                  workflow_id=outcome.workflow_id, status=outcome.status,
                                  failure_code=outcome.failure_code)  # fmt: skip
