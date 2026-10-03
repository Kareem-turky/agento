"""Human-approval management composition (Task 036), independent of the business backend.

    settings -> (database configured?)  no  -> no service (Approval routes answer 503)
             -> engine + sessions -> PostgresApprovalRepository
             -> GovernanceGate(APPROVAL_ACTIONS, HumanApproverPermissionEvaluator)
             -> decision handlers + ExecutionCoordinator (+ PostgresAuditSink)
             -> ApprovalService (observed through the ONE Product observability given by
                the composition root; the explicit Workflow continuation is the business
                composition's Workflow engine, when one exists)

Requests themselves are created only by the business composition's ExecutionCoordinator
(``ProductApprovalBroker``); nothing here can create one. Nothing here migrates or
creates tables.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.composition.contracts import DeploymentCompositionError
from app.config import Settings
from app.observability.contracts import ProductObservability

if TYPE_CHECKING:
    from app.approval_management.service import ApprovalService, ApprovalWorkflowResumer

RELEASE_FAILED = "approval resources could not be released"


async def _nothing() -> None:
    return None


@dataclass(frozen=True)
class ApprovalComposition:
    service: "ApprovalService | None" = None
    close: Callable[[], Awaitable[None]] = _nothing
    discard: Callable[[], None] = lambda: None


def build_approvals(
    settings: Settings,
    *,
    observability: ProductObservability | None = None,
    workflows: "ApprovalWorkflowResumer | None" = None,
) -> ApprovalComposition:
    if settings.database_url is None:
        return ApprovalComposition()
    # Imported only here (called after the business backend was allowed).
    from app.approval_management.actions import APPROVAL_ACTIONS
    from app.approval_management.handlers import build_approval_handlers
    from app.approval_management.permissions import HumanApproverPermissionEvaluator
    from app.approval_management.service import ApprovalService
    from app.execution import ActionHandlerRegistry, ExecutionCoordinator
    from app.governance import ActionCatalog, GovernanceGate
    from app.persistence import (
        PostgresApprovalRepository,
        PostgresAuditSink,
        create_product_engine,
        create_session_factory,
    )

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
        sessions = create_session_factory(engine)
        repository = PostgresApprovalRepository(sessions)
        gate = GovernanceGate(ActionCatalog(APPROVAL_ACTIONS),
                              permissions=HumanApproverPermissionEvaluator())  # fmt: skip
        handlers = ActionHandlerRegistry(build_approval_handlers(repository))
        coordinator = ExecutionCoordinator(gate, handlers, PostgresAuditSink(sessions))
        service = ApprovalService(repository, gate, coordinator, observability=observability,
                                  workflows=workflows)  # fmt: skip
        return ApprovalComposition(service=service, close=close, discard=discard)
    except BaseException:
        discard()
        raise
