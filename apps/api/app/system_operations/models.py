"""System operations vocabulary: fixed, low-cardinality states and codes only.

No value here can carry a URL, host, port, database or user name, path, identifier,
configuration value, exception text or secret.
"""

from dataclasses import dataclass
from enum import StrEnum


class ComponentState(StrEnum):
    READY = "ready"
    STARTING = "starting"
    UNAVAILABLE = "unavailable"
    MISMATCH = "mismatch"


class OverallStatus(StrEnum):
    READY = "ready"
    NOT_READY = "not_ready"


class NotReadyReason(StrEnum):
    """Stable reasons, reported only through authenticated System Status."""

    APPLICATION_STARTING = "application_starting"
    AGENT_RUNTIME_STARTING = "agent_runtime_starting"
    DATABASE_UNAVAILABLE = "database_unavailable"
    SCHEMA_MISMATCH = "schema_mismatch"
    SCHEMA_UNAVAILABLE = "schema_unavailable"


class TelemetryExportMode(StrEnum):
    """Product OpenTelemetry export (``APP_OTEL_EXPORT_MODE``); never the endpoint."""

    DISABLED = "disabled"
    OTLP_HTTP = "otlp_http"


@dataclass(frozen=True, slots=True)
class DatabaseCheck:
    """One bounded probe result. ``product_schema`` is UNAVAILABLE whenever the database
    itself is: a schema that cannot be read is never reported as current."""

    database: ComponentState
    product_schema: ComponentState


DATABASE_UNAVAILABLE = DatabaseCheck(ComponentState.UNAVAILABLE, ComponentState.UNAVAILABLE)


@dataclass(frozen=True, slots=True)
class LifecycleSnapshot:
    application_started: bool
    agent_runtime_attached: bool
    started_at: float | None  # monotonic seconds when the lifespan started


@dataclass(frozen=True, slots=True)
class SystemComponents:
    application: ComponentState
    database: ComponentState
    product_schema: ComponentState
    agent_runtime: ComponentState


@dataclass(frozen=True, slots=True)
class SystemStatus:
    version: str
    environment: str
    uptime_seconds: int
    overall: OverallStatus
    reasons: tuple[NotReadyReason, ...]
    components: SystemComponents
    export_mode: TelemetryExportMode
