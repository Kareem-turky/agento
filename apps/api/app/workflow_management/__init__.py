"""Product Workflow Platform (Task 034): deterministic, durable Product Workflows.

    definitions.py  immutable WorkflowDefinition / WorkflowStepDefinition (metadata only)
    catalog.py      the explicit, immutable ProductWorkflowCatalog
    state.py        run / step-attempt state machines and stable vocabularies
    records.py      durable CONTROL-state records (runs, attempts, events)
    handlers.py     trusted Step handler contract + explicit runtime registry
    contracts.py    the durable state contract (implemented by app.persistence)
    engine.py       WorkflowEngine: claim -> ordered Steps -> verify -> checkpoint
    service.py      WorkflowInspectionService: read-only catalog and run history
    actions.py      ``workflows.read`` governed read actions

Product-owned and runtime independent: no ``agno`` import, no model call, no provider,
no dynamic loading, no background worker. Concrete business Workflows (and their Step
handlers) live in ``app.workflows``.
"""

from app.workflow_management.catalog import (
    DAILY_REPORT_HANDLER_ID,
    DAILY_REPORT_STEP_ID,
    DAILY_REPORT_WORKFLOW,
    DAILY_REPORT_WORKFLOW_ID,
    ProductWorkflowCatalog,
    build_default_workflow_catalog,
)
from app.workflow_management.definitions import (
    CheckpointPolicy,
    StepSideEffect,
    WorkflowCategory,
    WorkflowDefinition,
    WorkflowInputField,
    WorkflowInputKind,
    WorkflowLifecycle,
    WorkflowStepDefinition,
)
from app.workflow_management.state import (
    StepAttemptStatus,
    VerificationCode,
    WorkflowEventType,
    WorkflowFailureCode,
    WorkflowRunStatus,
)

__all__ = [
    "DAILY_REPORT_HANDLER_ID",
    "DAILY_REPORT_STEP_ID",
    "DAILY_REPORT_WORKFLOW",
    "DAILY_REPORT_WORKFLOW_ID",
    "CheckpointPolicy",
    "ProductWorkflowCatalog",
    "StepAttemptStatus",
    "StepSideEffect",
    "VerificationCode",
    "WorkflowCategory",
    "WorkflowDefinition",
    "WorkflowEventType",
    "WorkflowFailureCode",
    "WorkflowInputField",
    "WorkflowInputKind",
    "WorkflowLifecycle",
    "WorkflowRunStatus",
    "WorkflowStepDefinition",
    "build_default_workflow_catalog",
]
