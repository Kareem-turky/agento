"""Product Tasks (Task 033): WHAT concrete Product job is requested or performed.

A ``TaskDefinition`` is immutable Product metadata: the Skills it requires, constrained
input-field metadata, explicit acceptance criteria (stable codes, never executable
expressions) and a declared execution envelope (``TaskLimits``).

v1 HONESTY: there is no Task executor yet. ``TaskLimits`` is Product CONTRACT metadata
checked for consistency (it may never exceed the owning Agent's real manifest); it is
not a new security boundary and grants nothing. The authoritative boundaries remain the
trusted run context, GovernanceGate, ExecutionCoordinator and the Agent's runtime
tool-call limit. A Task claiming a write never authorizes that write.

A Task MAY name the deterministic Product Workflow that performs it (``workflow_id``,
Task 034). Only a Task that really runs as a Product Workflow names one; the reference is
validated against the Workflow catalog when the capability graph is built.

``ProductTaskCatalog`` is the immutable, explicit list of Tasks in this build.
"""

from collections.abc import Iterable
from enum import StrEnum
from types import MappingProxyType
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from app.agent_management.definitions import AgentCategory, AgentLifecycle, DottedId

_FROZEN = ConfigDict(frozen=True, extra="forbid")
Name = Annotated[str, StringConstraints(strict=True, min_length=1, max_length=80)]
Text = Annotated[str, StringConstraints(strict=True, min_length=1, max_length=500)]
FieldName = Annotated[str, StringConstraints(strict=True, pattern=r"^[a-z][a-z0-9_]{0,63}$")]
CriterionCode = Annotated[str, StringConstraints(strict=True, pattern=r"^[a-z][a-z0-9_]{0,63}$")]


class TaskInputKind(StrEnum):
    """The only input-field kinds: metadata, never a schema engine or validator code."""

    TEXT = "text"
    UUID = "uuid"
    DATE = "date"
    BOOLEAN = "boolean"
    INTEGER = "integer"


class TaskInputField(BaseModel):
    model_config = _FROZEN

    name: FieldName
    label: Name
    kind: TaskInputKind
    required: bool
    description: Text
    max_length: int | None = Field(default=None, ge=1, le=10_000)

    @model_validator(mode="after")
    def _length_only_for_text(self) -> "TaskInputField":
        if self.max_length is not None and self.kind is not TaskInputKind.TEXT:
            raise ValueError("max_length applies to text fields only")
        return self


class TaskAcceptanceCriterion(BaseModel):
    """A stable, non-executable Product statement of what an acceptable result means."""

    model_config = _FROZEN

    code: CriterionCode
    description: Text


class TaskLimits(BaseModel):
    """Declared execution envelope (contract metadata, see the module docstring)."""

    model_config = _FROZEN

    max_tool_calls: int = Field(ge=1, le=50)
    writes_possible: bool
    allowed_write_actions: frozenset[DottedId] = frozenset()
    requires_explicit_write_intent: bool

    @model_validator(mode="after")
    def _consistent(self) -> "TaskLimits":
        if self.writes_possible:
            if not self.allowed_write_actions:
                raise ValueError("a write-capable task must name its allowed write actions")
            if not self.requires_explicit_write_intent:
                raise ValueError("a write-capable task must require explicit write intent")
        elif self.allowed_write_actions:
            raise ValueError("a read-only task cannot allow write actions")
        return self


class TaskDefinition(BaseModel):
    model_config = _FROZEN

    task_id: DottedId
    name: Name
    description: Text
    category: AgentCategory
    lifecycle: AgentLifecycle = AgentLifecycle.ACTIVE
    skill_ids: frozenset[DottedId] = Field(min_length=1)
    inputs: tuple[TaskInputField, ...] = ()
    acceptance_criteria: tuple[TaskAcceptanceCriterion, ...] = Field(min_length=1)
    limits: TaskLimits
    workflow_id: DottedId | None = None

    @model_validator(mode="after")
    def _unique(self) -> "TaskDefinition":
        names = [f.name for f in self.inputs]
        if len(names) != len(set(names)):
            raise ValueError("duplicate input field")
        codes = [c.code for c in self.acceptance_criteria]
        if len(codes) != len(set(codes)):
            raise ValueError("duplicate acceptance criterion")
        return self


class ProductTaskCatalog:
    """Immutable after construction; rejects duplicates and anything but definitions."""

    __slots__ = ("_tasks",)

    def __init__(self, tasks: Iterable[TaskDefinition]) -> None:
        by_id: dict[str, TaskDefinition] = {}
        for task in tasks:
            if not isinstance(task, TaskDefinition):
                raise TypeError("the catalog accepts TaskDefinition values only")
            if task.task_id in by_id:
                raise ValueError("duplicate task id")
            by_id[task.task_id] = task
        ordered = sorted(by_id.values(), key=lambda t: t.task_id)
        object.__setattr__(self, "_tasks", MappingProxyType({t.task_id: t for t in ordered}))

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError("ProductTaskCatalog is immutable")

    def get(self, task_id: str) -> TaskDefinition | None:
        return self._tasks.get(task_id)

    def definitions(self) -> tuple[TaskDefinition, ...]:
        return tuple(self._tasks.values())

    @property
    def task_ids(self) -> frozenset[str]:
        return frozenset(self._tasks)

    def __contains__(self, task_id: object) -> bool:
        return task_id in self._tasks

    def __len__(self) -> int:
        return len(self._tasks)


def _criteria(*pairs: tuple[str, str]) -> tuple[TaskAcceptanceCriterion, ...]:
    return tuple(TaskAcceptanceCriterion(code=c, description=d) for c, d in pairs)


def _read_only(max_tool_calls: int) -> TaskLimits:
    return TaskLimits(max_tool_calls=max_tool_calls, writes_possible=False,
                      requires_explicit_write_intent=False)  # fmt: skip


# ----- the real Tasks of this build: the Operations Agent's existing jobs ---------------------

INSPECT_ORDER_TASK = TaskDefinition(
    task_id="operations.inspect_order",
    name="Inspect an order",
    description="Analyze one order of the trusted store and its shipments.",
    category=AgentCategory.OPERATIONS,
    skill_ids=frozenset({"operations.order_inspection"}),
    inputs=(TaskInputField(name="order_id", label="Order", kind=TaskInputKind.UUID,
                           required=True, description="The canonical order UUID."),),
    acceptance_criteria=_criteria(
        ("facts_from_tools", "Order facts come only from the Product order tool."),
        ("shipment_facts_from_tools", "Shipment facts come only from the Product shipment tool."),
        ("store_scope_preserved", "Resources of another store are never exposed."),
        ("no_fabricated_results", "Unavailable or not-found results are reported, never "
                                  "invented."),
        ("no_provider_payloads", "Provider payloads and identifiers are never exposed."),
    ),
    limits=_read_only(4),
)  # fmt: skip
ANALYZE_DAILY_TASK = TaskDefinition(
    task_id="operations.analyze_daily",
    name="Analyze daily operations",
    description="Analyze the trusted store's operations for today or an explicitly "
    "supplied business date.",
    category=AgentCategory.OPERATIONS,
    skill_ids=frozenset({"operations.daily_analysis"}),
    inputs=(TaskInputField(name="business_date", label="Business date", kind=TaskInputKind.DATE,
                           required=False, description="Optional explicit date (YYYY-MM-DD); "
                           "omitted means the store's current business day."),),
    acceptance_criteria=_criteria(
        ("daily_report_used", "The daily operations report tool is used."),
        ("daily_report_authoritative", "The deterministic report is authoritative."),
        ("no_report_recalculation", "Metrics are never recalculated by the model."),
        ("no_invented_findings", "Findings not in the report are never added."),
        ("business_date_not_guessed", "The business date is never invented or computed."),
        ("coverage_respected", "Coverage limitations (for example inventory) are respected."),
        ("no_reconstruction_on_failure", "A denied or unavailable report is never "
                                         "reconstructed from individual tools."),
    ),
    limits=_read_only(2),
    # The daily report is the deterministic Product Workflow operations.daily_report.
    workflow_id="operations.daily_report",
)  # fmt: skip
ESCALATE_ISSUE_TASK = TaskDefinition(
    task_id="operations.escalate_issue",
    name="Escalate an operational issue",
    description="Request creation of a governed operational ticket for the trusted store.",
    category=AgentCategory.OPERATIONS,
    skill_ids=frozenset({"operations.ticket_escalation"}),
    inputs=(
        TaskInputField(name="title", label="Title", kind=TaskInputKind.TEXT, required=True,
                       description="Short ticket title.", max_length=160),
        TaskInputField(name="description", label="Description", kind=TaskInputKind.TEXT,
                       required=True, description="What the issue is and why it needs "
                       "follow-up.", max_length=4000),
    ),
    acceptance_criteria=_criteria(
        ("explicit_write_intent_required", "The trusted run must explicitly request the write."),
        ("agent_cannot_authorize_writes", "The Agent cannot manufacture write authorization."),
        ("governance_authoritative", "Product governance decides; a denial is final."),
        ("verified_write_only", "Only status verified means the ticket was created."),
        ("unconfirmed_is_not_created", "requires_human, denied, failed and awaiting_approval "
                                       "never count as a created ticket."),
    ),
    limits=TaskLimits(max_tool_calls=2, writes_possible=True,
                      allowed_write_actions=frozenset({"operations.ticket.create"}),
                      requires_explicit_write_intent=True),
)  # fmt: skip

DEFAULT_TASKS: tuple[TaskDefinition, ...] = (
    INSPECT_ORDER_TASK, ANALYZE_DAILY_TASK, ESCALATE_ISSUE_TASK,
)  # fmt: skip


def build_default_task_catalog() -> ProductTaskCatalog:
    return ProductTaskCatalog(DEFAULT_TASKS)
