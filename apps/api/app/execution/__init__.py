"""Governed execution foundation: govern -> validate -> execute -> verify -> audit.

Generic product core. It depends only on the standard library, Pydantic,
``app.context.models`` and ``app.governance``; no business actions live here.
"""

from app.execution.approvals import (
    ApprovalBroker,
    ApprovalChange,
    ApprovalClaim,
    ApprovalClaimStatus,
    ApprovalDescriber,
    ApprovalOutcome,
    ApprovalSource,
    ApprovalSourceRef,
    ApprovalSubject,
    ApprovalSummary,
)
from app.execution.audit import AuditEvent, AuditEventType, AuditSink
from app.execution.context import ActionExecutionContext
from app.execution.coordinator import ExecutionCoordinator
from app.execution.errors import (
    ActionExecutionError,
    ActionInputError,
    ExecutionFailedWithoutEffect,
    ExecutionOutcomeUncertain,
)
from app.execution.handlers import ActionHandler, ActionHandlerRegistry, RawParameters
from app.execution.models import (
    ActionRun,
    ActionRunReason,
    ActionRunStatus,
    ExecutionResult,
    VerificationResult,
)

__all__ = [
    "ApprovalBroker",
    "ApprovalChange",
    "ApprovalClaim",
    "ApprovalClaimStatus",
    "ApprovalDescriber",
    "ApprovalOutcome",
    "ApprovalSource",
    "ApprovalSourceRef",
    "ApprovalSubject",
    "ApprovalSummary",
    "ActionExecutionContext",
    "ActionExecutionError",
    "ActionHandler",
    "ActionHandlerRegistry",
    "ActionInputError",
    "ActionRun",
    "ActionRunReason",
    "ActionRunStatus",
    "AuditEvent",
    "AuditEventType",
    "AuditSink",
    "ExecutionCoordinator",
    "ExecutionFailedWithoutEffect",
    "ExecutionOutcomeUncertain",
    "ExecutionResult",
    "RawParameters",
    "VerificationResult",
]
