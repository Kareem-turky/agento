"""The durable approval state contract (Task 036), implemented by ``app.persistence``.

Every method is one short transaction scoped by the trusted company id IN THE QUERY:
another company's request is indistinguishable from a missing one. Every state change
is a compare-and-set on the current status and is committed TOGETHER with exactly one
append-only event (no "status changed but event missing").

Expiry is lazy and deterministic: ``expire_due`` moves every ``requested`` row whose
``expires_at <= now`` to ``expired`` (with its event), using the caller's injected clock
value; reads, lists, decisions and claims call it first. Nothing runs in the background.
"""

from datetime import datetime
from typing import Protocol, runtime_checkable
from uuid import UUID

from app.approval_management.models import ApprovalEvent, ApprovalRequest
from app.approval_management.state import ApprovalStatus
from app.execution.approvals import ApprovalClaimStatus, ApprovalOutcome


@runtime_checkable
class ApprovalRepository(Protocol):
    async def create(self, request: ApprovalRequest) -> None:
        """Insert a ``requested`` request and its ``requested`` event (one transaction)."""
        ...

    async def expire_due(self, company_id: str, now: datetime,
                         approval_id: UUID | None = None) -> int:  # fmt: skip
        """Expire due ``requested`` rows of the company (or just ``approval_id``)."""
        ...

    async def get(self, company_id: str, approval_id: UUID) -> ApprovalRequest | None: ...

    async def list(
        self, company_id: str, *, status: ApprovalStatus | None, action_name: str | None,
        limit: int,
    ) -> tuple[ApprovalRequest, ...]:  # fmt: skip
        """Newest first, bounded."""
        ...

    async def events(self, company_id: str, approval_id: UUID) -> tuple[ApprovalEvent, ...]: ...

    async def decide(
        self, company_id: str, approval_id: UUID, status: ApprovalStatus, actor_id: str,
        actor_type: str, note: str | None, now: datetime,
    ) -> ApprovalRequest:  # fmt: skip
        """``requested`` (and not due) -> ``status`` with its event. For approved and
        rejected the decider must differ from the requester (also enforced in SQL).
        Raises ``ApprovalTransitionConflictError`` when the CAS matches nothing."""
        ...

    async def claim(
        self, company_id: str, approval_id: UUID, *, store_id: str | None, action_name: str,
        requester_actor_id: str, requester_actor_type: str, subject_fingerprint: str,
        action_run_id: UUID, now: datetime,
    ) -> ApprovalClaimStatus:  # fmt: skip
        """Consume an approved, unconsumed, unexpired request whose subject matches
        EXACTLY (including the requester principal: actor id AND actor type),
        atomically (at most one caller ever gets ``CLAIMED``), with its
        ``execution_claimed`` event (naming that principal). Otherwise the reason,
        without changing anything."""
        ...

    async def record_execution(
        self, company_id: str, approval_id: UUID, action_run_id: UUID,
        outcome: ApprovalOutcome, now: datetime,
    ) -> None:  # fmt: skip
        """Correlate the consuming run's outcome (once) with its ``execution_completed``
        event."""
        ...
