"""Readiness evaluation: a pure function of the lifecycle and one database check.

Ready requires ALL of: the application lifespan started, the Agent runtime attached,
PostgreSQL reachable and the Product schema exactly at the expected revision. Business
features (Agents enabled, integrations, conversations, approvals, model providers) are
deliberately NOT readiness: they never make an instance unready.
"""

from app.system_operations.models import (
    ComponentState,
    DatabaseCheck,
    LifecycleSnapshot,
    NotReadyReason,
    OverallStatus,
    SystemComponents,
)


def evaluate(
    lifecycle: LifecycleSnapshot, database: DatabaseCheck
) -> tuple[SystemComponents, OverallStatus, tuple[NotReadyReason, ...]]:
    application = ComponentState.READY if lifecycle.application_started else ComponentState.STARTING
    runtime = (
        ComponentState.READY
        if lifecycle.application_started and lifecycle.agent_runtime_attached
        else ComponentState.STARTING
    )
    components = SystemComponents(
        application=application,
        database=database.database,
        product_schema=database.product_schema,
        agent_runtime=runtime,
    )
    reasons: list[NotReadyReason] = []
    if application is not ComponentState.READY:
        reasons.append(NotReadyReason.APPLICATION_STARTING)
    if runtime is not ComponentState.READY:
        reasons.append(NotReadyReason.AGENT_RUNTIME_STARTING)
    if database.database is not ComponentState.READY:
        reasons.append(NotReadyReason.DATABASE_UNAVAILABLE)
    if database.product_schema is ComponentState.MISMATCH:
        reasons.append(NotReadyReason.SCHEMA_MISMATCH)
    elif database.product_schema is not ComponentState.READY:
        reasons.append(NotReadyReason.SCHEMA_UNAVAILABLE)
    overall = OverallStatus.NOT_READY if reasons else OverallStatus.READY
    return components, overall, tuple(reasons)
