"""``WorkflowInspectionService``: READ-ONLY Workflow catalog and run history (Task 034).

    trusted RequestContext -> GovernanceGate (workflows.read) -> 403 / data

Identity and company come only from the trusted ``RequestContext``; every run query is
scoped by that company IN THE QUERY (a run of another company is indistinguishable from
a missing one). Nothing here executes, resumes or changes a Workflow, and no model, tool,
provider or network call is made. Execution is ``WorkflowEngine`` (internal only).
"""

from dataclasses import dataclass
from uuid import UUID

from app.context.models import ActorContext, RequestContext
from app.governance import (
    ActionDefinition,
    ActionIntent,
    ActionScope,
    GovernanceGate,
    PolicyOutcome,
)
from app.workflow_management import actions
from app.workflow_management.catalog import ProductWorkflowCatalog
from app.workflow_management.contracts import WorkflowRepositoryError, WorkflowRunRepository
from app.workflow_management.definitions import WorkflowDefinition
from app.workflow_management.errors import (
    WorkflowAccessForbiddenError,
    WorkflowNotFoundError,
    WorkflowRunNotFoundError,
    WorkflowUnavailableError,
)
from app.workflow_management.records import (
    StepAttemptRecord,
    WorkflowEventRecord,
    WorkflowRunRecord,
)

MAX_RUNS_LIMIT = 100


@dataclass(frozen=True, slots=True)
class WorkflowRunDetail:
    run: WorkflowRunRecord
    attempts: tuple[StepAttemptRecord, ...]
    events: tuple[WorkflowEventRecord, ...]


class WorkflowInspectionService:
    def __init__(
        self,
        catalog: ProductWorkflowCatalog,
        repository: WorkflowRunRepository,
        gate: GovernanceGate,
    ) -> None:
        if not isinstance(catalog, ProductWorkflowCatalog):
            raise TypeError("a ProductWorkflowCatalog is required")
        self._catalog = catalog
        self._repository = repository
        self._gate = gate

    def _authorize(self, context: RequestContext, action: ActionDefinition) -> ActorContext:
        actor = context.actor if isinstance(context, RequestContext) else None
        if actor is None:
            raise WorkflowAccessForbiddenError()
        decision = self._gate.decide(actor, ActionIntent(name=action.name),
                                     ActionScope(company_id=actor.company_id))  # fmt: skip
        if decision.outcome is not PolicyOutcome.ALLOW:
            raise WorkflowAccessForbiddenError()
        return actor

    # ----- catalog ----------------------------------------------------------------------------

    def catalog(self, context: RequestContext) -> tuple[WorkflowDefinition, ...]:
        self._authorize(context, actions.CATALOG_READ)
        return self._catalog.definitions()

    def get_workflow(self, context: RequestContext, workflow_id: str) -> WorkflowDefinition:
        self._authorize(context, actions.CATALOG_READ)
        definition = self._catalog.get(workflow_id)
        if definition is None:
            raise WorkflowNotFoundError()
        return definition

    # ----- run history ------------------------------------------------------------------------

    async def list_runs(
        self, context: RequestContext, limit: int = 25
    ) -> tuple[tuple[WorkflowRunRecord, int], ...]:
        """Recent runs of the caller's company, newest first, with their attempt counts."""
        actor = self._authorize(context, actions.RUNS_READ)
        if type(limit) is not int or not 1 <= limit <= MAX_RUNS_LIMIT:
            raise ValueError("limit out of range")
        try:
            return await self._repository.list_runs(actor.company_id, limit)
        except WorkflowRepositoryError:
            raise WorkflowUnavailableError() from None

    async def get_run(self, context: RequestContext, run_id: UUID) -> WorkflowRunDetail:
        actor = self._authorize(context, actions.RUNS_READ)
        try:
            run = await self._repository.get_run(actor.company_id, run_id)
            if run is None:
                raise WorkflowRunNotFoundError()
            attempts = await self._repository.list_attempts(actor.company_id, run_id)
            events = await self._repository.list_events(actor.company_id, run_id)
        except WorkflowRepositoryError:
            raise WorkflowUnavailableError() from None
        return WorkflowRunDetail(run=run, attempts=attempts, events=events)
