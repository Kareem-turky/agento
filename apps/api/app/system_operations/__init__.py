"""Product system operations (Task 039): liveness, readiness and System Status.

    liveness   "is this API process alive?"           no dependency is touched
    readiness  "can this instance serve Product work?" lifespan started, Agent runtime
                                                        attached, PostgreSQL reachable,
                                                        Product schema at the expected head
    status     "why (not) ready, and what is up?"      Product-authenticated, system.read

Observes and diagnoses the Product; it never changes it. Nothing here runs an Agent,
calls a model, tests an integration, migrates, backs up, restores or restarts anything.
FastAPI and SQLAlchemy stay outside this package (routes and ``app.persistence``).
"""

from app.system_operations.contracts import DatabaseReadinessProbe, LifecycleSource
from app.system_operations.errors import SystemAccessDeniedError, SystemOperationsError
from app.system_operations.models import (
    ComponentState,
    DatabaseCheck,
    LifecycleSnapshot,
    NotReadyReason,
    OverallStatus,
    SystemComponents,
    SystemStatus,
    TelemetryExportMode,
)
from app.system_operations.permissions import SYSTEM_ACTIONS, SYSTEM_READ, SYSTEM_READ_PERMISSION
from app.system_operations.readiness import evaluate
from app.system_operations.revision import EXPECTED_PRODUCT_SCHEMA_REVISION
from app.system_operations.service import SystemOperationsService

__all__ = [
    "EXPECTED_PRODUCT_SCHEMA_REVISION",
    "SYSTEM_ACTIONS",
    "SYSTEM_READ",
    "SYSTEM_READ_PERMISSION",
    "ComponentState",
    "DatabaseCheck",
    "DatabaseReadinessProbe",
    "LifecycleSnapshot",
    "LifecycleSource",
    "NotReadyReason",
    "OverallStatus",
    "SystemAccessDeniedError",
    "SystemComponents",
    "SystemOperationsError",
    "SystemOperationsService",
    "SystemStatus",
    "TelemetryExportMode",
    "evaluate",
]
