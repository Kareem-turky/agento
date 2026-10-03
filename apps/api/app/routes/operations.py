"""``POST /api/v1/operations/runs``: the product HTTP boundary of the Operations Agent.

    HTTP -> RequestContextMiddleware -> ActorResolver -> trusted ActorContext (401 if none)
         -> strict body {message, store_id}
         -> exact store grant check against actor.store_ids (403 otherwise)
         -> trusted ActionScope(company_id=actor.company_id, store_id=<granted store>)
         -> OperationsRunService.run_product (READ-ONLY) -> {request_id, message}
            (409 "Operations Agent is disabled" when Agent management disabled it: the
            refusal happens before the Agent, its model or any tool runs)

Product authentication is the ``ActorResolver``; the AgentOS ``OS_SECURITY_KEY`` is
not product authentication. The client never supplies identity, company,
permissions, write intent or run context. This route writes nothing: the service
runs with no requested write actions. It depends on no agent runtime, integration,
execution or handler.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, StringConstraints

from app.context import CurrentActor, CurrentRequestContext
from app.governance import ActionScope
from app.routes.validation import SafeValidationRoute
from app.services.operations import (
    OperationsAgentDisabledError,
    OperationsRunService,
    ProductOperationsRunResult,
)

OPERATIONS_SERVICE_STATE_KEY = "operations_service"
OPERATIONS_RUNS_PATH = "/api/v1/operations/runs"
MAX_MESSAGE_LENGTH = 8000

router = APIRouter(tags=["operations"], route_class=SafeValidationRoute)


class OperationsRunRequest(BaseModel):
    """The only client-controlled inputs: the message and a target store selector."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    message: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_MESSAGE_LENGTH)
    ]
    store_id: UUID


class OperationsRunResponse(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    request_id: UUID
    message: str


def _service(request: Request) -> OperationsRunService | None:
    service = getattr(request.app.state, OPERATIONS_SERVICE_STATE_KEY, None)
    return service if isinstance(service, OperationsRunService) else None


@router.post(OPERATIONS_RUNS_PATH, response_model=OperationsRunResponse)
async def create_operations_run(
    body: OperationsRunRequest,
    context: CurrentRequestContext,
    actor: CurrentActor,
    request: Request,
) -> OperationsRunResponse:
    # The store is a client-selected target; it becomes trusted scope only if the
    # authenticated actor was explicitly granted exactly this store.
    store_id = str(body.store_id)
    if store_id not in actor.store_ids:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")
    scope = ActionScope(company_id=actor.company_id, store_id=store_id)

    service = _service(request)
    if service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Operations service unavailable",
        )
    # Everything that depends on the service's answer stays inside the fail-closed
    # boundary: a runtime-checkable Protocol does not guarantee the result type.
    try:
        result = await service.run_product(context, scope, body.message)
        if not isinstance(result, ProductOperationsRunResult):
            raise TypeError("invalid operations service result")
        response = OperationsRunResponse(request_id=context.request_id, message=result.message)
    except OperationsAgentDisabledError:
        # A deliberate installation setting, not an outage: stable and value-free.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Operations Agent is disabled"
        ) from None
    except Exception:  # noqa: BLE001 - never leak internals; the request id correlates logs
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Operations service unavailable",
        ) from None
    return response
