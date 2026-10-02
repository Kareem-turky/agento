"""The daily operations report on the Product Workflow Platform (Task 034).

    DailyOperationsReportService (route + Operations Agent tool: unchanged contract)
      -> WorkflowBackedDailyOperationsReportService
      -> WorkflowEngine.execute("operations.daily_report", trusted request, trusted scope)
      -> Step "compute_daily_report" -> DailyReportStepHandler (read-only)
      -> the EXISTING DailyOperationsWorkflow (unchanged: it alone computes the report)

The platform adds durable execution control (run, attempt, events), a step timeout,
verification and observability. It never recomputes, copies or persists the report:
the report is the Step's EPHEMERAL output, handed back to this caller in memory. The
Step checkpoints nothing. Externally the contract is the same: the same report, a
denial is ``DailyOperationsForbiddenError`` and anything else that goes wrong is
``DailyOperationsUnavailableError``.
"""

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, field_validator

from app.context.models import RequestContext
from app.governance import ActionScope
from app.services.operations_reports import (
    DailyOperationsForbiddenError,
    DailyOperationsReport,
    DailyOperationsReportService,
    DailyOperationsUnavailableError,
)
from app.workflow_management.catalog import (
    DAILY_REPORT_HANDLER_ID,
    DAILY_REPORT_STEP_ID,
    DAILY_REPORT_WORKFLOW_ID,
)
from app.workflow_management.engine import WorkflowEngine
from app.workflow_management.errors import WorkflowAccessDeniedError
from app.workflow_management.handlers import (
    Checkpoints,
    ReadOnlyStepHandler,
    StepDeniedError,
    StepFailedError,
    StepOutcome,
    StepResult,
    WorkflowRuntimeRegistration,
    WorkflowStepContext,
)
from app.workflow_management.state import WorkflowFailureCode, WorkflowRunStatus


class DailyReportInput(BaseModel):
    """The Workflow's typed input: the optional explicit business date, nothing else
    (store, company and identity are trusted context, never input)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    business_date: date | None = None

    @field_validator("business_date", mode="before")
    @classmethod
    def _date_only(cls, value: object) -> object:
        if isinstance(value, datetime):
            raise ValueError("a business date is a date, not a datetime")
        return value


class DailyReportStepHandler(ReadOnlyStepHandler):
    """Runs the existing deterministic report; verifies it is the requested one."""

    handler_id = DAILY_REPORT_HANDLER_ID

    def __init__(self, report_service: DailyOperationsReportService) -> None:
        self._report_service = report_service

    @property
    def report_service(self) -> DailyOperationsReportService:
        """The wrapped deterministic implementation (the existing workflow)."""
        return self._report_service

    async def execute(
        self, context: WorkflowStepContext, run_input: BaseModel, checkpoints: Checkpoints
    ) -> StepResult:
        if not isinstance(run_input, DailyReportInput):
            raise StepFailedError()
        try:
            report = await self._report_service.get_daily_report(
                context.request, context.scope, run_input.business_date
            )
        except DailyOperationsForbiddenError:
            raise StepDeniedError() from None
        except DailyOperationsUnavailableError:
            raise StepFailedError() from None  # confirmed: nothing read is trusted
        return StepResult(StepOutcome.COMPLETED, output=report)

    async def verify(
        self,
        context: WorkflowStepContext,
        run_input: BaseModel,
        checkpoints: Checkpoints,
        result: StepResult,
    ) -> bool:
        report = result.output
        if not isinstance(report, DailyOperationsReport) or not isinstance(
            run_input, DailyReportInput
        ):
            return False
        if context.store_id is None or str(report.store_id) != context.store_id:
            return False  # never another store's report
        return run_input.business_date is None or report.business_date == run_input.business_date


def daily_report_registration(
    report_service: DailyOperationsReportService,
) -> WorkflowRuntimeRegistration:
    return WorkflowRuntimeRegistration(
        workflow_id=DAILY_REPORT_WORKFLOW_ID,
        input_model=DailyReportInput,
        handlers=(DailyReportStepHandler(report_service),),
    )


class WorkflowBackedDailyOperationsReportService:
    """``DailyOperationsReportService`` executed as the ``operations.daily_report``
    Product Workflow (one durable run per report request)."""

    def __init__(self, engine: WorkflowEngine) -> None:
        if not isinstance(engine, WorkflowEngine):
            raise TypeError("a WorkflowEngine is required")
        self._engine = engine

    @property
    def engine(self) -> WorkflowEngine:
        return self._engine

    async def get_daily_report(
        self, request: RequestContext, scope: ActionScope, business_date: date | None
    ) -> DailyOperationsReport:
        if not isinstance(request, RequestContext) or not isinstance(scope, ActionScope):
            raise DailyOperationsUnavailableError()
        if business_date is not None and (
            not isinstance(business_date, date) or isinstance(business_date, datetime)
        ):
            raise DailyOperationsUnavailableError()
        try:
            result = await self._engine.execute(
                DAILY_REPORT_WORKFLOW_ID, request, scope,
                DailyReportInput(business_date=business_date),
            )  # fmt: skip
        except WorkflowAccessDeniedError:
            raise DailyOperationsForbiddenError() from None
        except Exception:  # noqa: BLE001 - the report contract's single "unavailable"
            raise DailyOperationsUnavailableError() from None
        if result.status is WorkflowRunStatus.SUCCEEDED:
            report = result.output(DAILY_REPORT_STEP_ID)
            if isinstance(report, DailyOperationsReport):
                return report
        if result.failure_code is WorkflowFailureCode.ACCESS_DENIED:
            raise DailyOperationsForbiddenError()
        raise DailyOperationsUnavailableError()
