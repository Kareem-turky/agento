"""``GET /api/v1/operations/reports/daily``: the deterministic daily operations report.

    HTTP -> RequestContextMiddleware -> ActorResolver -> trusted ActorContext (401 if none)
         -> query: store_id (UUID), business_date (YYYY-MM-DD, optional); nothing else
         -> exact store grant check against actor.store_ids             (403 otherwise)
         -> trusted ActionScope(company_id=actor.company_id, store_id=<granted store>)
         -> DailyOperationsReportService.get_daily_report                (none: 503)
         -> {request_id, report}

Not an agent endpoint: no model is involved; the report is computed by a
deterministic workflow behind the service contract. A missing report permission is
403; anything else that goes wrong is 503 with a fixed message. The client never
supplies identity, company, timezone, permissions or report content; the store's own
timezone decides the business day. Product authentication is the ``ActorResolver``,
never the AgentOS ``OS_SECURITY_KEY``.
"""

from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict

from app.context import CurrentActor, CurrentRequestContext
from app.governance import ActionScope
from app.services.operations_reports import (
    DailyOperationsForbiddenError,
    DailyOperationsReport,
    DailyOperationsReportService,
)

OPERATIONS_DAILY_REPORT_SERVICE_STATE_KEY = "daily_operations_service"
OPERATIONS_DAILY_REPORT_PATH = "/api/v1/operations/reports/daily"
ALLOWED_QUERY_PARAMETERS = frozenset({"store_id", "business_date"})
UNAVAILABLE = "Daily operations report unavailable"

router = APIRouter(tags=["operations"])


class DailyOperationsReportResponse(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    request_id: UUID
    report: DailyOperationsReport


def _service(request: Request) -> DailyOperationsReportService | None:
    service = getattr(request.app.state, OPERATIONS_DAILY_REPORT_SERVICE_STATE_KEY, None)
    return service if isinstance(service, DailyOperationsReportService) else None


def _unavailable() -> HTTPException:
    return HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=UNAVAILABLE)


@router.get(OPERATIONS_DAILY_REPORT_PATH, response_model=DailyOperationsReportResponse)
async def get_daily_operations_report(
    store_id: Annotated[UUID, Query()],
    context: CurrentRequestContext,
    actor: CurrentActor,
    request: Request,
    business_date: Annotated[date | None, Query()] = None,
) -> DailyOperationsReportResponse:
    # Only the two documented parameters, each at most once (no timezone, company,
    # identity or report fields can be smuggled in).
    params = request.query_params
    if set(params) - ALLOWED_QUERY_PARAMETERS or any(len(params.getlist(k)) > 1 for k in params):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                            detail="Unsupported query parameters")  # fmt: skip
    # The store is a client-selected target; it becomes trusted scope only if the
    # authenticated actor was explicitly granted exactly this store.
    target = str(store_id)
    if target not in actor.store_ids:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")
    scope = ActionScope(company_id=actor.company_id, store_id=target)
    service = _service(request)
    if service is None:
        raise _unavailable()
    try:
        report = await service.get_daily_report(context, scope, business_date)
        if not isinstance(report, DailyOperationsReport):
            raise TypeError("invalid daily operations report")
        response = DailyOperationsReportResponse(request_id=context.request_id, report=report)
    except DailyOperationsForbiddenError:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden") from None
    except Exception:  # noqa: BLE001 - never leak internals; the request id correlates logs
        raise _unavailable() from None
    return response
