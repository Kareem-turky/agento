"""``SystemOperationsService``: readiness and Product-authenticated System Status.

    readiness()       public, minimal: True only when every readiness component is ready.
                      Evaluated FRESH on every call (no cache), so a database outage and
                      its recovery are both seen by the same running process.
    status(context)   trusted RequestContext -> GovernanceGate (system.read) -> 403
                      -> components, overall, stable reasons, version, environment,
                         uptime and the telemetry export MODE (never its endpoint)

Read-only. Never runs an Agent or model, calls an integration, migrates, backs up,
restores, restarts or reconfigures anything. Every probe is bounded by its probe.
"""

import time
from collections.abc import Callable

from app.context.models import RequestContext
from app.governance import ActionCatalog, ActionIntent, ActionScope, GovernanceGate, PolicyOutcome
from app.observability.contracts import (
    BusinessDetails,
    ObservationDetails,
    ObservationOutcome,
    ProductObservability,
    ProductOperation,
    observe,
)
from app.system_operations.contracts import DatabaseReadinessProbe, LifecycleSource
from app.system_operations.errors import SystemAccessDeniedError
from app.system_operations.models import (
    DATABASE_UNAVAILABLE,
    DatabaseCheck,
    LifecycleSnapshot,
    OverallStatus,
    SystemStatus,
    TelemetryExportMode,
)
from app.system_operations.permissions import SYSTEM_ACTIONS, SYSTEM_READ
from app.system_operations.readiness import evaluate

MonotonicClock = Callable[[], float]


class SystemOperationsService:
    def __init__(
        self,
        *,
        probe: DatabaseReadinessProbe | None,
        lifecycle: LifecycleSource,
        version: str,
        environment: str,
        export_mode: TelemetryExportMode,
        monotonic: MonotonicClock = time.monotonic,
        observability: ProductObservability | None = None,
        gate: GovernanceGate | None = None,
    ) -> None:
        self._probe = probe
        self._lifecycle = lifecycle
        # The fixed system.read catalog; injectable for tests only.
        self._gate = gate if gate is not None else GovernanceGate(ActionCatalog(SYSTEM_ACTIONS))
        self._version = version
        self._environment = environment
        self._export_mode = TelemetryExportMode(export_mode)
        self._monotonic = monotonic
        self._observability = observability

    async def _database(self) -> DatabaseCheck:
        if self._probe is None:
            return DATABASE_UNAVAILABLE  # no database configured: never ready
        try:
            return await self._probe.check()
        except Exception:  # noqa: BLE001 - a probe defect is "unavailable", never a 500
            return DATABASE_UNAVAILABLE

    def _snapshot(self) -> LifecycleSnapshot:
        try:
            return self._lifecycle()
        except Exception:  # noqa: BLE001 - an unreadable lifecycle is "starting"
            return LifecycleSnapshot(False, False, None)

    async def readiness(self) -> bool:
        lifecycle = self._snapshot()
        if not (lifecycle.application_started and lifecycle.agent_runtime_attached):
            return False  # not serving yet: no database round trip needed
        _, overall, _ = evaluate(lifecycle, await self._database())
        return overall is OverallStatus.READY

    async def status(self, context: RequestContext) -> SystemStatus:
        with observe(self._observability, ProductOperation.SYSTEM_STATUS,
                     context.request_id) as obs:  # fmt: skip
            actor = context.actor
            allowed = actor is not None and self._gate.decide(
                actor, ActionIntent(name=SYSTEM_READ.name),
                ActionScope(company_id=actor.company_id),
            ).outcome is PolicyOutcome.ALLOW  # fmt: skip
            if not allowed:
                obs.finish(ObservationOutcome.DENIED)
                raise SystemAccessDeniedError()
            lifecycle = self._snapshot()
            components, overall, reasons = evaluate(lifecycle, await self._database())
            uptime = 0
            if lifecycle.application_started and lifecycle.started_at is not None:
                uptime = max(0, int(self._monotonic() - lifecycle.started_at))
            obs.finish(ObservationOutcome.COMPLETED,
                       ObservationDetails(business=BusinessDetails(status=overall)))  # fmt: skip
            return SystemStatus(
                version=self._version,
                environment=self._environment,
                uptime_seconds=uptime,
                overall=overall,
                reasons=reasons,
                components=components,
                export_mode=self._export_mode,
            )
