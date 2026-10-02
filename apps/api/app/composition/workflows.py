"""Workflow inspection composition (Task 034), independent of the business backend.

    settings -> (database configured?)  no  -> no service (Workflow routes answer 503)
             -> ProductWorkflowCatalog (static; built before any resource is acquired)
             -> engine + sessions -> PostgresWorkflowRunRepository
             -> GovernanceGate(WORKFLOW_ACTIONS)        (``workflows.read``)
             -> WorkflowInspectionService               (read-only)

Execution is NOT composed here: Workflows run only inside trusted business compositions
(for example the daily operations report). Nothing here migrates or creates tables.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.composition.contracts import DeploymentCompositionError
from app.config import Settings

if TYPE_CHECKING:
    from app.workflow_management.service import WorkflowInspectionService

RELEASE_FAILED = "workflow inspection resources could not be released"


async def _nothing() -> None:
    return None


@dataclass(frozen=True)
class WorkflowInspectionComposition:
    service: "WorkflowInspectionService | None" = None
    close: Callable[[], Awaitable[None]] = _nothing
    discard: Callable[[], None] = lambda: None


def build_workflow_inspection(settings: Settings) -> WorkflowInspectionComposition:
    if settings.database_url is None:
        return WorkflowInspectionComposition()
    # Imported only here (called after the business backend was allowed).
    from app.governance import ActionCatalog, GovernanceGate
    from app.persistence import (
        PostgresWorkflowRunRepository,
        create_product_engine,
        create_session_factory,
    )
    from app.workflow_management.actions import WORKFLOW_ACTIONS
    from app.workflow_management.catalog import build_default_workflow_catalog
    from app.workflow_management.service import WorkflowInspectionService

    catalog = build_default_workflow_catalog()  # before any resource is acquired
    engine = create_product_engine(str(settings.database_url))
    released = False

    async def close() -> None:
        nonlocal released
        if released:
            return
        released = True
        try:
            await engine.dispose()
        except Exception:  # noqa: BLE001 - never surface driver/URL details
            raise DeploymentCompositionError(RELEASE_FAILED) from None

    def discard() -> None:
        nonlocal released
        if released:
            return
        released = True
        try:
            engine.sync_engine.dispose()
        except Exception:  # noqa: BLE001 - never surface driver/URL details
            raise DeploymentCompositionError(RELEASE_FAILED) from None

    try:
        repository = PostgresWorkflowRunRepository(create_session_factory(engine))
        gate = GovernanceGate(ActionCatalog(WORKFLOW_ACTIONS))
        service = WorkflowInspectionService(catalog, repository, gate)
        return WorkflowInspectionComposition(service=service, close=close, discard=discard)
    except BaseException:
        discard()
        raise
