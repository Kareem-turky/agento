"""``ProductWorkflowCatalog``: the immutable, explicit list of Product Workflows.

Static registrations only: no dynamic import, entry point, directory scan, database row,
remote registration, HTTP mutation or model-generated definition. Duplicates and anything
but a ``WorkflowDefinition`` are rejected.

This build installs exactly ONE Product Workflow, ``operations.daily_report``: the
existing deterministic daily operations analysis (``DailyOperationsWorkflow``), run as a
single read-only Step. No placeholder Workflow exists for anything not implemented.
"""

from collections.abc import Iterable
from types import MappingProxyType

from app.workflow_management.definitions import (
    CheckpointPolicy,
    StepSideEffect,
    WorkflowCategory,
    WorkflowDefinition,
    WorkflowInputField,
    WorkflowInputKind,
    WorkflowStepDefinition,
)


class ProductWorkflowCatalog:
    """Immutable after construction; rejects duplicates and anything but definitions."""

    __slots__ = ("_workflows",)

    def __init__(self, workflows: Iterable[WorkflowDefinition]) -> None:
        by_id: dict[str, WorkflowDefinition] = {}
        for workflow in workflows:
            if not isinstance(workflow, WorkflowDefinition):
                raise TypeError("the catalog accepts WorkflowDefinition values only")
            if workflow.workflow_id in by_id:
                raise ValueError("duplicate workflow id")
            by_id[workflow.workflow_id] = workflow
        ordered = sorted(by_id.values(), key=lambda w: w.workflow_id)
        object.__setattr__(self, "_workflows",
                           MappingProxyType({w.workflow_id: w for w in ordered}))  # fmt: skip

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError("ProductWorkflowCatalog is immutable")

    def get(self, workflow_id: str) -> WorkflowDefinition | None:
        return self._workflows.get(workflow_id)

    def definitions(self) -> tuple[WorkflowDefinition, ...]:
        return tuple(self._workflows.values())

    @property
    def workflow_ids(self) -> frozenset[str]:
        return frozenset(self._workflows)

    def __contains__(self, workflow_id: object) -> bool:
        return workflow_id in self._workflows

    def __len__(self) -> int:
        return len(self._workflows)


# ----- the real Product Workflow of this build ----------------------------------------------

DAILY_REPORT_WORKFLOW_ID = "operations.daily_report"
DAILY_REPORT_STEP_ID = "compute_daily_report"
DAILY_REPORT_HANDLER_ID = "operations.daily_report.compute"

DAILY_REPORT_WORKFLOW = WorkflowDefinition(
    workflow_id=DAILY_REPORT_WORKFLOW_ID,
    name="Daily operations report",
    description="The deterministic daily operations analysis of one trusted store: "
    "governance preflight, store-local business day, governed order and shipment reads, "
    "canonical metrics and rule-based findings.",
    category=WorkflowCategory.OPERATIONS,
    version=1,
    inputs=(WorkflowInputField(name="business_date", label="Business date",
                               kind=WorkflowInputKind.DATE, required=False,
                               description="Optional explicit date (YYYY-MM-DD); omitted "
                               "means the store's current business day."),),
    steps=(
        WorkflowStepDefinition(
            step_id=DAILY_REPORT_STEP_ID,
            name="Compute the daily report",
            description="Runs the existing deterministic DailyOperationsWorkflow. The report "
            "is returned to the caller in memory and never persisted.",
            handler_id=DAILY_REPORT_HANDLER_ID,
            side_effect=StepSideEffect.READ_ONLY,
            timeout_seconds=30,
            # One attempt: the report keeps its existing externally observable behaviour
            # (a failed report is answered as unavailable, never silently re-read).
            max_attempts=1,
            checkpoint_policy=CheckpointPolicy.NONE,
        ),
    ),
)  # fmt: skip

DEFAULT_WORKFLOWS: tuple[WorkflowDefinition, ...] = (DAILY_REPORT_WORKFLOW,)


def build_default_workflow_catalog() -> ProductWorkflowCatalog:
    return ProductWorkflowCatalog(DEFAULT_WORKFLOWS)
