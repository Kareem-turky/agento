"""Product health and System Status API (Task 039).

    GET /health/live            public   200 {"status":"alive"}             no dependency
    GET /health/ready           public   200 {"status":"ready"}
                                         503 {"status":"not_ready"}         never a reason
    GET /api/v1/system/status   Product-authenticated, ``system.read``      read-only

The public answers disclose nothing but the status word: no version, environment,
framework, revision, host, reason or identifier. Detailed component states and stable
reasons are only in the authenticated System Status, which itself never contains a URL,
host, path, identifier, configuration value, endpoint, secret or exception text. The
legacy ``GET /health`` (in ``app.main``) is unchanged and backward-compatible.

There is no write route of any kind: nothing here restarts, migrates, backs up,
restores or reconfigures the installation.
"""

from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from app.context import CurrentActor, CurrentRequestContext
from app.routes.integrations import SafeValidationRoute
from app.system_operations import (
    ComponentState,
    NotReadyReason,
    OverallStatus,
    SystemAccessDeniedError,
    SystemOperationsService,
    SystemStatus,
    TelemetryExportMode,
)

SYSTEM_SERVICE_STATE_KEY = "system_operations_service"
HEALTH_LIVE_PATH = "/health/live"
HEALTH_READY_PATH = "/health/ready"
SYSTEM_STATUS_PATH = "/api/v1/system/status"
# Public: no Product or AgentOS credential is needed (container health checks).
PUBLIC_HEALTH_PATHS = (HEALTH_LIVE_PATH, HEALTH_READY_PATH)
# Product-authenticated (ActorResolver), never AgentOS-key-authenticated.
SYSTEM_PATHS = (SYSTEM_STATUS_PATH,)

router = APIRouter(tags=["system"], route_class=SafeValidationRoute)
_FROZEN = ConfigDict(frozen=True, extra="forbid")
_NO_STORE = {"Cache-Control": "no-store"}


class ApplicationView(BaseModel):
    model_config = _FROZEN
    version: str = Field(description="The Agento Product version.")
    environment: Literal["local", "test", "staging", "production"]
    uptime_seconds: int = Field(ge=0, description="Seconds since this process started serving.")


class ComponentsView(BaseModel):
    model_config = _FROZEN
    application: ComponentState
    database: ComponentState
    product_schema: ComponentState
    agent_runtime: ComponentState


class ObservabilityView(BaseModel):
    model_config = _FROZEN
    export_mode: TelemetryExportMode = Field(
        description="Product OpenTelemetry export mode (never its endpoint)."
    )


class SystemStatusResponse(BaseModel):
    model_config = _FROZEN
    request_id: UUID
    application: ApplicationView
    overall: OverallStatus
    reasons: list[NotReadyReason] = Field(description="Stable codes; empty when ready.")
    components: ComponentsView
    observability: ObservabilityView

    @classmethod
    def of(cls, request_id: UUID, found: SystemStatus) -> "SystemStatusResponse":
        c = found.components
        return cls(
            request_id=request_id,
            application=ApplicationView(version=found.version, environment=found.environment,  # type: ignore[arg-type]
                                        uptime_seconds=found.uptime_seconds),
            overall=found.overall,
            reasons=list(found.reasons),
            components=ComponentsView(application=c.application, database=c.database,
                                      product_schema=c.product_schema,
                                      agent_runtime=c.agent_runtime),
            observability=ObservabilityView(export_mode=found.export_mode),
        )  # fmt: skip


def _service(request: Request) -> SystemOperationsService | None:
    service = getattr(request.app.state, SYSTEM_SERVICE_STATE_KEY, None)
    return service if isinstance(service, SystemOperationsService) else None


@router.get(HEALTH_LIVE_PATH, include_in_schema=False)
async def live() -> JSONResponse:
    # Liveness only: no database, model, integration, configuration or version.
    return JSONResponse({"status": "alive"}, headers=_NO_STORE)


@router.get(HEALTH_READY_PATH, include_in_schema=False)
async def ready(request: Request) -> JSONResponse:
    service = _service(request)
    try:
        is_ready = service is not None and await service.readiness()
    except Exception:  # noqa: BLE001 - fail closed, never a reason or a 500
        is_ready = False
    if is_ready:
        return JSONResponse({"status": "ready"}, headers=_NO_STORE)
    return JSONResponse({"status": "not_ready"}, status_code=503, headers=_NO_STORE)


_ERRORS: dict[int | str, dict[str, Any]] = {
    401: {"description": "Missing or invalid Product API key."},
    403: {"description": "The actor lacks system.read."},
    503: {"description": "System status unavailable."},
}


@router.get(SYSTEM_STATUS_PATH, response_model=SystemStatusResponse, responses=_ERRORS,
            summary="System status",
            description="Installation-level operational status: component states, stable "
                        "not-ready reasons, version, environment, uptime and the telemetry "
                        "export mode. Read-only.\n\nRequires the `system.read` Product "
                        "permission.")  # fmt: skip
async def system_status(
    context: CurrentRequestContext, actor: CurrentActor, request: Request, response: Response
) -> SystemStatusResponse:
    service = _service(request)
    if service is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail="System status unavailable")
    try:
        found = await service.status(context)
    except SystemAccessDeniedError:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Forbidden") from None
    except Exception:  # noqa: BLE001 - fixed, value-free answer
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail="System status unavailable") from None  # fmt: skip
    response.headers.update(_NO_STORE)
    return SystemStatusResponse.of(context.request_id, found)
