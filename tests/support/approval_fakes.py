"""TEST-ONLY human-approval helpers (Task 036). Never used by production code.

* ``InMemoryApprovalRepository``: the ``ApprovalRepository`` contract with the same
  compare-and-set semantics as ``PostgresApprovalRepository`` (proven against real
  PostgreSQL in tests/integration/test_approvals_postgres.py).
* TEST-ONLY MEDIUM_RISK / HIGH_RISK actions and approval-capable handlers. They are never
  registered in a production ActionCatalog: Task 036 adds no real high-risk business
  action. ``BudgetHandler`` describes a before/after change (budget 100 -> 150).
* ``ManualClock``: an injectable, movable clock for deterministic expiry.
"""

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr

from app.approval_management.errors import (
    ApprovalRepositoryError,
    ApprovalTransitionConflictError,
)
from app.approval_management.models import ApprovalEvent, ApprovalRequest
from app.approval_management.state import (
    EVENT_FOR_STATUS,
    HUMAN_DECISIONS,
    ApprovalEventType,
    ApprovalStatus,
)
from app.context.models import ActorContext, RequestContext
from app.execution import (
    ActionExecutionContext,
    ApprovalChange,
    ApprovalClaimStatus,
    ApprovalOutcome,
    ApprovalSummary,
    ExecutionResult,
    VerificationResult,
)
from app.governance import ActionDefinition, ActionRisk, ActionScopeRequirement

COMPANY = "00000000-0000-4000-8000-0000000000c1"
OTHER_COMPANY = "00000000-0000-4000-8000-0000000000c2"
STORE_A = "00000000-0000-4000-8000-00000000a001"
STORE_B = "00000000-0000-4000-8000-00000000b002"
T0 = datetime(2031, 5, 1, 9, 0, tzinfo=UTC)

# TEST-ONLY actions (never in a production catalog).
BUDGET_UPDATE = ActionDefinition(
    name="test.budget.update", description="TEST-ONLY: change a campaign budget.",
    risk=ActionRisk.MEDIUM_RISK, required_permission="budgets.update",
    scope_requirement=ActionScopeRequirement.STORE,
)  # fmt: skip
PAYOUT_RELEASE = ActionDefinition(
    name="test.payout.release", description="TEST-ONLY: release a payout.",
    risk=ActionRisk.HIGH_RISK, required_permission="payouts.release",
    scope_requirement=ActionScopeRequirement.COMPANY,
)  # fmt: skip
UNDESCRIBED = ActionDefinition(
    name="test.undescribed.write", description="TEST-ONLY: cannot describe an approval.",
    risk=ActionRisk.HIGH_RISK, required_permission="payouts.release",
    scope_requirement=ActionScopeRequirement.COMPANY,
)  # fmt: skip
TEST_ACTIONS = (BUDGET_UPDATE, PAYOUT_RELEASE, UNDESCRIBED)

REQUESTER = frozenset({"budgets.update", "payouts.release", "approvals.read",
                       "approvals.cancel"})  # fmt: skip
APPROVER = frozenset({"approvals.read", "approvals.decide", "approvals.cancel"})


class ManualClock:
    def __init__(self, start: datetime = T0) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **delta: float) -> None:
        self.now += timedelta(**delta)


def actor(actor_id: str = "requester-1", permissions: frozenset[str] = REQUESTER, *,
          company: str = COMPANY, actor_type: str = "user",
          stores: frozenset[str] = frozenset({STORE_A})) -> ActorContext:  # fmt: skip
    return ActorContext(actor_id=actor_id, actor_type=actor_type, company_id=company,
                        permissions=permissions, store_ids=stores)  # fmt: skip


def request(who: ActorContext | None) -> RequestContext:
    return RequestContext(actor=who, channel="api")


# ----- test-only approval-capable handlers ---------------------------------------------------


class BudgetInput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    campaign: StrictStr = Field(min_length=1, max_length=40)
    amount: StrictInt = Field(ge=1, le=1_000_000)
    reason: StrictStr = Field(min_length=1, max_length=200)


class BudgetHandler:
    """TEST-ONLY MEDIUM_RISK write: a store-scoped campaign budget (100 by default)."""

    action_name = BUDGET_UPDATE.name

    def __init__(self) -> None:
        self.budgets: dict[tuple[str | None, str], int] = {}
        self.effects: list[tuple[str | None, str, int]] = []
        self.validate_calls = 0

    def current(self, store: str | None, campaign: str) -> int:
        return self.budgets.get((store, campaign), 100)

    def validate(self, parameters: Mapping[str, Any]) -> BaseModel:
        self.validate_calls += 1
        return BudgetInput.model_validate(dict(parameters))

    def describe_approval(
        self, context: ActionExecutionContext, validated_input: BaseModel
    ) -> ApprovalSummary:
        data = cast(BudgetInput, validated_input)
        return ApprovalSummary(
            title="Change campaign budget",
            description=f"Campaign {data.campaign}: {data.reason}",
            changes=(ApprovalChange(code="budget", label="Budget",
                                    before=str(self.current(context.store_id, data.campaign)),
                                    after=str(data.amount)),),
        )  # fmt: skip

    async def execute(self, context: ActionExecutionContext, validated_input: BaseModel
                      ) -> ExecutionResult:  # fmt: skip
        data = cast(BudgetInput, validated_input)
        self.budgets[(context.store_id, data.campaign)] = data.amount
        self.effects.append((context.store_id, data.campaign, data.amount))
        return ExecutionResult(reference_id=f"budget-{len(self.effects)}")

    async def verify(self, context: ActionExecutionContext, validated_input: BaseModel,
                     execution_result: ExecutionResult | None) -> VerificationResult:  # fmt: skip
        data = cast(BudgetInput, validated_input)
        ok = self.budgets.get((context.store_id, data.campaign)) == data.amount
        return VerificationResult(verified=ok, reason_code="budget_set" if ok else "budget_unset")


class PayoutInput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    payout: StrictStr = Field(min_length=1, max_length=40)
    amount: StrictInt = Field(ge=1, le=1_000_000)


class PayoutHandler:
    """TEST-ONLY HIGH_RISK write (company scope)."""

    action_name = PAYOUT_RELEASE.name

    def __init__(self) -> None:
        self.released: list[tuple[str, int]] = []

    def validate(self, parameters: Mapping[str, Any]) -> BaseModel:
        return PayoutInput.model_validate(dict(parameters))

    def describe_approval(self, context: ActionExecutionContext, validated_input: BaseModel
                          ) -> ApprovalSummary:  # fmt: skip
        data = cast(PayoutInput, validated_input)
        return ApprovalSummary(title="Release payout", description=f"Payout {data.payout}",
                               changes=(ApprovalChange(code="payout.amount", label="Amount",
                                                       before=None,
                                                       after=str(data.amount)),))  # fmt: skip

    async def execute(self, context: ActionExecutionContext, validated_input: BaseModel
                      ) -> ExecutionResult:  # fmt: skip
        data = cast(PayoutInput, validated_input)
        self.released.append((data.payout, data.amount))
        return ExecutionResult(reference_id=f"payout-{len(self.released)}")

    async def verify(self, context: ActionExecutionContext, validated_input: BaseModel,
                     execution_result: ExecutionResult | None) -> VerificationResult:  # fmt: skip
        data = cast(PayoutInput, validated_input)
        ok = (data.payout, data.amount) in self.released
        return VerificationResult(verified=ok, reason_code="released" if ok else "missing")


class UndescribedHandler:
    """TEST-ONLY HIGH_RISK handler WITHOUT ``describe_approval``: must fail closed."""

    action_name = UNDESCRIBED.name

    def __init__(self) -> None:
        self.executed = 0

    def validate(self, parameters: Mapping[str, Any]) -> BaseModel:
        return PayoutInput.model_validate(dict(parameters))

    async def execute(self, context: ActionExecutionContext, validated_input: BaseModel
                      ) -> ExecutionResult:  # fmt: skip
        self.executed += 1
        return ExecutionResult(reference_id="never")

    async def verify(self, context: ActionExecutionContext, validated_input: BaseModel,
                     execution_result: ExecutionResult | None) -> VerificationResult:  # fmt: skip
        return VerificationResult(verified=False, reason_code="never")


# ----- in-memory approval repository ----------------------------------------------------------


class InMemoryApprovalRepository:
    def __init__(self) -> None:
        self.rows: dict[UUID, ApprovalRequest] = {}
        self.history: list[ApprovalEvent] = []
        self.fail = False

    def _check(self) -> None:
        if self.fail:
            raise ApprovalRepositoryError()

    def _event(self, approval: ApprovalRequest, event_type: ApprovalEventType, at: datetime,
               **fields: Any) -> None:  # fmt: skip
        sequence = 1 + sum(1 for e in self.history if e.approval_id == approval.approval_id)
        self.history.append(ApprovalEvent(
            approval_id=approval.approval_id, company_id=approval.company_id,
            sequence=sequence, event_type=event_type, status=approval.status,
            occurred_at=at, **fields))  # fmt: skip

    async def create(self, request: ApprovalRequest) -> None:
        self._check()
        if request.approval_id in self.rows or request.status is not ApprovalStatus.REQUESTED:
            raise ApprovalRepositoryError()
        self.rows[request.approval_id] = request
        self._event(request, ApprovalEventType.REQUESTED, request.created_at,
                    actor_id=request.requester_actor_id,
                    actor_type=request.requester_actor_type,
                    action_run_id=request.action_run_id)  # fmt: skip

    async def expire_due(self, company_id: str, now: datetime,
                         approval_id: UUID | None = None) -> int:  # fmt: skip
        self._check()
        count = 0
        for key, row in list(self.rows.items()):
            if (row.company_id == company_id and row.status is ApprovalStatus.REQUESTED
                    and row.expires_at <= now
                    and (approval_id is None or key == approval_id)):  # fmt: skip
                expired = row.model_copy(update={"status": ApprovalStatus.EXPIRED,
                                                 "decided_at": now})  # fmt: skip
                self.rows[key] = expired
                self._event(expired, ApprovalEventType.EXPIRED, now)
                count += 1
        return count

    async def get(self, company_id: str, approval_id: UUID) -> ApprovalRequest | None:
        self._check()
        row = self.rows.get(approval_id)
        return row if row is not None and row.company_id == company_id else None

    async def list(self, company_id: str, *, status: ApprovalStatus | None,
                   action_name: str | None, limit: int) -> tuple[ApprovalRequest, ...]:  # fmt: skip
        self._check()
        rows = [r for r in self.rows.values() if r.company_id == company_id
                and (status is None or r.status is status)
                and (action_name is None or r.action_name == action_name)]  # fmt: skip
        rows.sort(key=lambda r: (-r.created_at.timestamp(), str(r.approval_id)))
        return tuple(rows[:limit])

    async def events(self, company_id: str, approval_id: UUID) -> tuple[ApprovalEvent, ...]:
        self._check()
        return tuple(e for e in self.history
                     if e.approval_id == approval_id and e.company_id == company_id)  # fmt: skip

    async def decide(self, company_id: str, approval_id: UUID, status: ApprovalStatus,
                     actor_id: str, actor_type: str, note: str | None,
                     now: datetime) -> ApprovalRequest:  # fmt: skip
        self._check()
        row = await self.get(company_id, approval_id)
        if (row is None or row.status is not ApprovalStatus.REQUESTED or row.expires_at <= now
                or status not in HUMAN_DECISIONS or actor_type == "system_agent"
                or (status is not ApprovalStatus.CANCELLED
                    and row.requester_actor_id == actor_id)):  # fmt: skip
            raise ApprovalTransitionConflictError()
        decided = row.model_copy(update={
            "status": status, "decided_at": now, "decided_by_actor_id": actor_id,
            "decided_by_actor_type": actor_type, "decision_note": note})  # fmt: skip
        ApprovalRequest.model_validate(decided.model_dump())  # the same invariants as SQL
        self.rows[approval_id] = decided
        self._event(decided, EVENT_FOR_STATUS[status], now, actor_id=actor_id,
                    actor_type=actor_type)  # fmt: skip
        return decided

    async def claim(self, company_id: str, approval_id: UUID, *, store_id: str | None,
                    action_name: str, requester_actor_id: str, subject_fingerprint: str,
                    action_run_id: UUID, now: datetime) -> ApprovalClaimStatus:  # fmt: skip
        self._check()
        row = await self.get(company_id, approval_id)
        if row is None:
            return ApprovalClaimStatus.NOT_FOUND
        if (row.action_name, row.requester_actor_id, row.store_id,
                row.subject_fingerprint) != (action_name, requester_actor_id, store_id,
                                             subject_fingerprint):  # fmt: skip
            return ApprovalClaimStatus.MISMATCH
        refusal = {ApprovalStatus.REQUESTED: ApprovalClaimStatus.NOT_DECIDED,
                   ApprovalStatus.REJECTED: ApprovalClaimStatus.REJECTED,
                   ApprovalStatus.CANCELLED: ApprovalClaimStatus.CANCELLED,
                   ApprovalStatus.EXPIRED: ApprovalClaimStatus.EXPIRED}.get(row.status)  # fmt: skip
        if refusal is not None:
            return refusal
        if row.consumed:
            return ApprovalClaimStatus.ALREADY_CONSUMED
        if row.expires_at <= now:
            return ApprovalClaimStatus.EXPIRED
        consumed = row.model_copy(update={"consumed_at": now,
                                          "consumed_by_action_run_id": action_run_id})  # fmt: skip
        self.rows[approval_id] = consumed
        self._event(consumed, ApprovalEventType.EXECUTION_CLAIMED, now,
                    actor_id=requester_actor_id, action_run_id=action_run_id)  # fmt: skip
        return ApprovalClaimStatus.CLAIMED

    async def record_execution(self, company_id: str, approval_id: UUID, action_run_id: UUID,
                               outcome: ApprovalOutcome, now: datetime) -> None:  # fmt: skip
        self._check()
        row = await self.get(company_id, approval_id)
        if (row is None or row.consumed_by_action_run_id != action_run_id
                or row.execution_outcome is not None):  # fmt: skip
            return
        done = row.model_copy(update={"execution_outcome": ApprovalOutcome(outcome)})
        self.rows[approval_id] = done
        self._event(done, ApprovalEventType.EXECUTION_COMPLETED, now,
                    action_run_id=action_run_id, execution_outcome=outcome)  # fmt: skip


# ----- a complete in-memory Approval world ----------------------------------------------------


class ApprovalWorld:
    """Real ProductApprovalBroker, ExecutionCoordinators, governance and ApprovalService
    over the in-memory approval repository and recording audit sinks."""

    def __init__(self, *, observability: Any = None, clock: ManualClock | None = None,
                 repository: Any = None, audit: Any = None,
                 decision_audit: Any = None) -> None:  # fmt: skip
        from app.approval_management.actions import APPROVAL_ACTIONS
        from app.approval_management.broker import ProductApprovalBroker
        from app.approval_management.handlers import build_approval_handlers
        from app.approval_management.permissions import HumanApproverPermissionEvaluator
        from app.approval_management.service import ApprovalService
        from app.execution import ActionHandlerRegistry, ExecutionCoordinator
        from app.governance import ActionCatalog, GovernanceGate
        from tests.support.integration_fakes import RecordingAuditSink

        self.clock = clock if clock is not None else ManualClock()
        self.repository = repository if repository is not None else InMemoryApprovalRepository()
        self.broker = ProductApprovalBroker(self.repository, clock=self.clock,
                                            observability=observability)  # fmt: skip
        self.budget, self.payout, self.undescribed = (BudgetHandler(), PayoutHandler(),
                                                      UndescribedHandler())  # fmt: skip
        self.audit = audit if audit is not None else RecordingAuditSink()
        self.catalog = ActionCatalog(TEST_ACTIONS)
        self.coordinator = ExecutionCoordinator(
            GovernanceGate(self.catalog),
            ActionHandlerRegistry([self.budget, self.payout, self.undescribed]),
            self.audit, clock=self.clock, approvals=self.broker,
        )  # fmt: skip
        self.decision_audit = (
            decision_audit if decision_audit is not None else (RecordingAuditSink())
        )
        gate = GovernanceGate(ActionCatalog(APPROVAL_ACTIONS),
                              permissions=HumanApproverPermissionEvaluator())  # fmt: skip
        decisions = ExecutionCoordinator(
            gate, ActionHandlerRegistry(build_approval_handlers(self.repository,
                                                                clock=self.clock)),
            self.decision_audit, clock=self.clock,
        )  # fmt: skip
        self.service = ApprovalService(self.repository, gate, decisions, clock=self.clock,
                                       observability=observability)  # fmt: skip

    async def run_budget(
        self, who: ActorContext, *, amount: int = 150, store: str = STORE_A,
        approval_id: UUID | None = None, campaign: str = "spring",
    ) -> Any:  # fmt: skip
        from app.governance import ActionIntent, ActionScope

        return await self.coordinator.run(
            request(who), ActionIntent(name=BUDGET_UPDATE.name),
            ActionScope(company_id=who.company_id, store_id=store),
            {"campaign": campaign, "amount": amount, "reason": "Spring sale"},
            approval_id=approval_id,
        )  # fmt: skip
