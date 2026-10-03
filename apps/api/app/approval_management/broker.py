"""``ProductApprovalBroker``: the Product implementation of the execution
``ApprovalBroker`` port (Task 036).

* ``request``: called by ``ExecutionCoordinator`` ONLY when governance returned
  REQUIRE_APPROVAL (there is no public way to create a request). It derives the subject
  fingerprint from the frozen validated input and stores the request with a Product-owned
  TTL (``DEFAULT_APPROVAL_TTL``, never from a request, model, document or provider).
* ``claim``: recomputes the fingerprint from the NEW validated input and consumes a
  granted request for exactly that subject, once (database compare-and-set).
* ``record_execution``: correlation metadata only.

Observability uses the ONE Product observability given by the composition root (fixed
operation names, enum labels only: never an id, actor, note, summary or parameter).
"""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import BaseModel

from app.approval_management.contracts import ApprovalRepository
from app.approval_management.errors import ApprovalRepositoryError
from app.approval_management.fingerprint import subject_fingerprint
from app.approval_management.models import APPROVAL_RISKS, ApprovalRequest
from app.approval_management.state import ApprovalStatus
from app.execution.approvals import (
    ApprovalClaim,
    ApprovalClaimStatus,
    ApprovalOutcome,
    ApprovalSubject,
)
from app.observability.contracts import (
    BusinessDetails,
    ObservationDetails,
    ObservationOutcome,
    ProductObservability,
    ProductOperation,
    observe,
)

DEFAULT_APPROVAL_TTL = timedelta(hours=24)


def utc_now() -> datetime:
    return datetime.now(UTC)


class ApprovalRequestOutcome(StrEnum):
    RECORDED = "recorded"
    UNAVAILABLE = "unavailable"


class ProductApprovalBroker:
    def __init__(
        self,
        repository: ApprovalRepository,
        *,
        clock: Callable[[], datetime] = utc_now,
        new_id: Callable[[], UUID] = uuid4,
        ttl: timedelta = DEFAULT_APPROVAL_TTL,
        observability: ProductObservability | None = None,
    ) -> None:
        if not timedelta(minutes=1) <= ttl <= timedelta(days=30):
            raise ValueError("the approval TTL must be between 1 minute and 30 days")
        self._repository = repository
        self._clock = clock
        self._new_id = new_id
        self._ttl = ttl
        self._observability = observability

    def _now(self) -> datetime:
        now = self._clock()
        if not isinstance(now, datetime) or now.utcoffset() is None:
            raise ApprovalRepositoryError()  # a naive clock fails closed
        return now

    async def request(self, subject: ApprovalSubject, validated_input: BaseModel) -> UUID:
        with observe(self._observability, ProductOperation.APPROVAL_REQUEST,
                     subject.request_id) as obs:  # fmt: skip
            try:
                if subject.risk not in APPROVAL_RISKS:
                    raise ValueError("only MEDIUM_RISK and HIGH_RISK actions are approved")
                now = self._now()
                approval = ApprovalRequest(
                    approval_id=self._new_id(), company_id=subject.company_id,
                    store_id=subject.store_id, action_name=subject.action_name,
                    risk=subject.risk, requester_actor_id=subject.requester_actor_id,
                    requester_actor_type=subject.requester_actor_type,
                    request_id=subject.request_id, action_run_id=subject.action_run_id,
                    subject_fingerprint=subject_fingerprint(
                        action_name=subject.action_name,
                        requester_actor_id=subject.requester_actor_id,
                        company_id=subject.company_id, store_id=subject.store_id,
                        validated_input=validated_input),
                    status=ApprovalStatus.REQUESTED, summary=subject.summary,
                    source=subject.source, created_at=now, expires_at=now + self._ttl,
                )  # fmt: skip
                await self._repository.create(approval)
            except Exception:
                obs.finish(ObservationOutcome.UNAVAILABLE, _details(
                    ApprovalRequestOutcome.UNAVAILABLE, subject))  # fmt: skip
                raise
            obs.finish(ObservationOutcome.COMPLETED,
                       _details(ApprovalRequestOutcome.RECORDED, subject))  # fmt: skip
            return approval.approval_id

    async def claim(self, claim: ApprovalClaim, validated_input: BaseModel) -> ApprovalClaimStatus:
        with observe(self._observability, ProductOperation.APPROVAL_CONSUME) as obs:
            try:
                now = self._now()
                fingerprint = subject_fingerprint(
                    action_name=claim.action_name, requester_actor_id=claim.requester_actor_id,
                    company_id=claim.company_id, store_id=claim.store_id,
                    validated_input=validated_input,
                )  # fmt: skip
                await self._repository.expire_due(claim.company_id, now, claim.approval_id)
                status = await self._repository.claim(
                    claim.company_id, claim.approval_id, store_id=claim.store_id,
                    action_name=claim.action_name, requester_actor_id=claim.requester_actor_id,
                    subject_fingerprint=fingerprint, action_run_id=claim.action_run_id, now=now,
                )  # fmt: skip
            except Exception:
                obs.finish(ObservationOutcome.UNAVAILABLE)
                raise
            claimed = status is ApprovalClaimStatus.CLAIMED
            obs.finish(ObservationOutcome.COMPLETED if claimed else ObservationOutcome.DENIED,
                       ObservationDetails(business=BusinessDetails(status=status)))  # fmt: skip
            return status

    async def record_execution(
        self, company_id: str, approval_id: UUID, action_run_id: UUID, outcome: ApprovalOutcome
    ) -> None:
        await self._repository.record_execution(company_id, approval_id, action_run_id,
                                                ApprovalOutcome(outcome), self._now())  # fmt: skip


def _details(outcome: ApprovalRequestOutcome, subject: ApprovalSubject) -> ObservationDetails:
    return ObservationDetails(business=BusinessDetails(status=outcome, reason=subject.risk))
