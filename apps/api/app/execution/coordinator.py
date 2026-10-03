"""ExecutionCoordinator: govern -> validate -> execute -> verify -> audit.

    create run id -> audit REQUESTED
    -> GovernanceGate -> audit POLICY_DECIDED
       DENY             -> audit DENIED             -> DENIED
       REQUIRE_APPROVAL -> human approval (Task 036, below)
       ALLOW
    -> handler lookup (missing -> FAILED handler_not_registered)
    -> validate raw parameters (invalid -> FAILED input_invalid)
    -> build the trusted ActionExecutionContext (once; passed to execute AND verify)
    -> audit EXECUTION_STARTED (must succeed, else FAILED audit_unavailable: no execute)
    -> execute (the only side-effect boundary)
         confirmed no effect -> FAILED (the only attempted execute that skips verify)
         uncertain -> audit EXECUTION_FAILED -> audit VERIFICATION_STARTED
                   -> verify(validated, None) as recovery evidence
                   -> REQUIRES_HUMAN execution_outcome_uncertain (never VERIFIED)
    -> audit EXECUTION_COMPLETED -> audit VERIFICATION_STARTED
    -> verify(validated, result) (always runs, even if an audit write failed)
    -> VERIFIED only if verified AND every audit write succeeded, else REQUIRES_HUMAN

ALLOW is permission to attempt execution, not proof of success.

REQUIRE_APPROVAL (Task 036). Governance (CURRENT permission and policy) is always
evaluated first, so a human approval is an ADDITIONAL condition and never replaces a
permission: a denied actor is DENIED whatever approval id it presents.

    -> handler lookup -> validate (as above)
    no approval_id: request a human decision
       handler is no ApprovalDescriber / summary invalid / broker unavailable
                        -> audit APPROVAL_REFUSED -> FAILED approval_unavailable
       -> broker.request(subject, validated input)  (durable; no raw parameters)
       -> audit AWAITING_APPROVAL -> AWAITING_APPROVAL + approval_id  (nothing runs)
    approval_id: execute under a granted request, at most once
       -> broker.claim(exact subject: action, requester, company, store, validated input)
          anything but CLAIMED -> audit APPROVAL_REFUSED -> FAILED approval_<reason>
       -> the ALLOW path from EXECUTION_STARTED (the request stays consumed even if that
          audit write fails: a new request is needed; conservative by design)
       -> broker.record_execution (correlation only; never changes the run)
"""

from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel

from app.context.models import RequestContext
from app.execution.approvals import (
    ApprovalBroker,
    ApprovalClaim,
    ApprovalClaimStatus,
    ApprovalDescriber,
    ApprovalOutcome,
    ApprovalSourceRef,
    ApprovalSubject,
    ApprovalSummary,
)
from app.execution.audit import AuditEvent, AuditEventType, AuditSink
from app.execution.context import ActionExecutionContext
from app.execution.errors import ActionExecutionError
from app.execution.handlers import ActionHandler, ActionHandlerRegistry
from app.execution.models import (
    ActionRun,
    ActionRunReason,
    ActionRunStatus,
    ExecutionResult,
    VerificationResult,
)
from app.governance import ActionIntent, ActionScope, GovernanceGate, PolicyDecision, PolicyOutcome


def _utc_now() -> datetime:
    return datetime.now(UTC)


class _Run:
    """Per-run state: identity, audit bookkeeping and result construction."""

    def __init__(
        self,
        coordinator: "ExecutionCoordinator",
        request: RequestContext,
        intent: ActionIntent,
        scope: ActionScope,
    ) -> None:
        self.coordinator = coordinator
        self.request = request
        self.scope = scope
        self.run_id: UUID = coordinator._new_id()
        self.action_name = intent.name
        self.audit_complete = True
        self.policy: PolicyDecision | None = None
        self.approval_id: UUID | None = None

    async def audit(self, event_type: AuditEventType, **fields: Any) -> bool:
        actor = self.request.actor
        try:
            event = AuditEvent(
                event_id=self.coordinator._new_id(),
                run_id=self.run_id,
                request_id=self.request.request_id,
                occurred_at=self.coordinator._clock(),
                event_type=event_type,
                action_name=self.action_name,
                actor_id=actor.actor_id if actor else None,
                actor_type=actor.actor_type if actor else None,
                company_id=self.scope.company_id,
                store_id=self.scope.store_id,
                channel=self.request.channel,
                policy_outcome=self.policy.outcome if self.policy else None,
                policy_reason=self.policy.reason if self.policy else None,
                approval_id=self.approval_id,
                **fields,
            )
            await self.coordinator._audit.record(event)
        except Exception:  # noqa: BLE001 - any sink failure is an audit failure
            self.audit_complete = False
            return False
        return True

    def execution_context(self, action_name: str) -> ActionExecutionContext:
        """The trusted handler context: only request, scope and run identity, never
        anything from raw parameters."""
        actor = self.request.actor
        return ActionExecutionContext(
            run_id=self.run_id,
            request_id=self.request.request_id,
            action_name=action_name,
            actor_id=actor.actor_id if actor else None,
            actor_type=actor.actor_type if actor else None,
            company_id=self.scope.company_id,
            store_id=self.scope.store_id,
            channel=self.request.channel,
            session_id=self.request.session_id,
        )

    def result(
        self,
        status: ActionRunStatus,
        reason: ActionRunReason,
        execution: ExecutionResult | None = None,
        verification: VerificationResult | None = None,
    ) -> ActionRun:
        return ActionRun(
            run_id=self.run_id,
            request_id=self.request.request_id,
            action_name=self.action_name,
            status=status,
            reason=reason,
            policy_decision=self.policy,
            execution_result=execution,
            verification_result=verification,
            audit_complete=self.audit_complete,
            approval_id=self.approval_id,
        )

    async def finish(
        self,
        event_type: AuditEventType,
        status: ActionRunStatus,
        reason: ActionRunReason,
        execution: ExecutionResult | None = None,
        verification: VerificationResult | None = None,
    ) -> ActionRun:
        await self.audit(
            event_type,
            run_status=status,
            run_reason=reason,
            execution_reference_id=execution.reference_id if execution else None,
            verification_code=verification.reason_code if verification else None,
        )
        return self.result(status, reason, execution, verification)


_CLAIM_REFUSALS: Mapping[ApprovalClaimStatus | None, ActionRunReason] = MappingProxyType(
    {
        ApprovalClaimStatus.NOT_FOUND: ActionRunReason.APPROVAL_NOT_FOUND,
        ApprovalClaimStatus.NOT_DECIDED: ActionRunReason.APPROVAL_NOT_DECIDED,
        ApprovalClaimStatus.REJECTED: ActionRunReason.APPROVAL_REJECTED,
        ApprovalClaimStatus.EXPIRED: ActionRunReason.APPROVAL_EXPIRED,
        ApprovalClaimStatus.CANCELLED: ActionRunReason.APPROVAL_CANCELLED,
        ApprovalClaimStatus.MISMATCH: ActionRunReason.APPROVAL_MISMATCH,
        ApprovalClaimStatus.ALREADY_CONSUMED: ActionRunReason.APPROVAL_ALREADY_CONSUMED,
    }
)
_OUTCOMES: Mapping[ActionRunStatus, ApprovalOutcome] = MappingProxyType(
    {
        ActionRunStatus.VERIFIED: ApprovalOutcome.VERIFIED,
        ActionRunStatus.REQUIRES_HUMAN: ApprovalOutcome.REQUIRES_HUMAN,
        ActionRunStatus.FAILED: ApprovalOutcome.FAILED,
        ActionRunStatus.DENIED: ApprovalOutcome.FAILED,
        ActionRunStatus.AWAITING_APPROVAL: ApprovalOutcome.FAILED,
    }
)


class ExecutionCoordinator:
    def __init__(
        self,
        gate: GovernanceGate,
        handlers: ActionHandlerRegistry,
        audit: AuditSink,
        *,
        clock: Callable[[], datetime] = _utc_now,
        id_factory: Callable[[], UUID] = uuid4,
        approvals: ApprovalBroker | None = None,
    ) -> None:
        self._gate = gate
        self._handlers = handlers
        self._audit = audit
        self._clock = clock
        self._new_id = id_factory
        # Task 036: the durable human-approval broker. None: an action that needs a
        # human decision fails closed (approval_unavailable) and never runs.
        self._approvals = approvals

    async def run(
        self,
        request: RequestContext,
        intent: ActionIntent,
        scope: ActionScope,
        parameters: Mapping[str, Any],
        *,
        approval_id: UUID | None = None,
        source: ApprovalSourceRef | None = None,
    ) -> ActionRun:
        """Govern and, only if allowed, validate, execute, verify and audit one action.

        ``request`` (and its actor) and ``scope`` are trusted backend context;
        ``intent``, ``parameters`` and ``approval_id`` are untrusted. Parameters only
        ever reach the handler's ``validate``; an approval id grants nothing by itself.
        ``source`` is trusted correlation metadata for a new approval request.
        """
        run = _Run(self, request, intent, scope)
        S, R, E = ActionRunStatus, ActionRunReason, AuditEventType

        if not await run.audit(E.REQUESTED):
            return run.result(S.FAILED, R.AUDIT_UNAVAILABLE)

        run.policy = self._gate.decide(request.actor, intent, scope)
        policy_recorded = await run.audit(E.POLICY_DECIDED)

        if run.policy.outcome is PolicyOutcome.DENY:
            return await run.finish(E.DENIED, S.DENIED, R.POLICY_DENIED)
        gated = run.policy.outcome is PolicyOutcome.REQUIRE_APPROVAL
        if (not gated and run.policy.outcome is not PolicyOutcome.ALLOW) or not policy_recorded:
            return run.result(S.FAILED, R.AUDIT_UNAVAILABLE)

        handler = self._handlers.get(run.policy.action_name)
        if handler is None:
            return await run.finish(E.HANDLER_NOT_REGISTERED, S.FAILED, R.HANDLER_NOT_REGISTERED)

        validated = self._validate(handler, parameters)
        if isinstance(validated, ActionRunReason):
            return await run.finish(E.VALIDATION_FAILED, S.FAILED, validated)

        context = run.execution_context(run.policy.action_name)
        if not gated:
            return await self._execute(run, handler, context, validated)
        if self._approvals is None:
            return await run.finish(E.APPROVAL_REFUSED, S.FAILED, R.APPROVAL_UNAVAILABLE)
        if approval_id is None:
            return await self._request_approval(run, handler, context, validated, source)
        return await self._execute_granted(run, handler, context, validated, approval_id)

    async def _request_approval(
        self,
        run: _Run,
        handler: ActionHandler,
        context: ActionExecutionContext,
        validated: BaseModel,
        source: ApprovalSourceRef | None,
    ) -> ActionRun:
        """Record a durable request for a human decision. Nothing executes; anything
        missing (summary, broker, durable write) fails closed WITHOUT an approval id."""
        S, R, E = ActionRunStatus, ActionRunReason, AuditEventType
        actor, broker, policy = run.request.actor, self._approvals, run.policy
        if broker is None or actor is None or policy is None or policy.risk is None:
            return await run.finish(E.APPROVAL_REFUSED, S.FAILED, R.APPROVAL_UNAVAILABLE)
        try:
            if not isinstance(handler, ApprovalDescriber):
                raise TypeError("the handler cannot describe an approval")
            summary = handler.describe_approval(context, validated)
            if not isinstance(summary, ApprovalSummary):
                raise TypeError("invalid approval summary")
            subject = ApprovalSubject(
                company_id=run.scope.company_id, store_id=run.scope.store_id,
                action_name=policy.action_name, risk=policy.risk,
                requester_actor_id=actor.actor_id, requester_actor_type=actor.actor_type,
                request_id=run.request.request_id, action_run_id=run.run_id, summary=summary,
                source=source if isinstance(source, ApprovalSourceRef) else ApprovalSourceRef(),
            )  # fmt: skip
            approval_id = await broker.request(subject, validated)
            if not isinstance(approval_id, UUID):
                raise TypeError("invalid approval id")
        except Exception:  # noqa: BLE001 - no durable request: fail closed, nothing runs
            return await run.finish(E.APPROVAL_REFUSED, S.FAILED, R.APPROVAL_UNAVAILABLE)
        run.approval_id = approval_id
        return await run.finish(E.AWAITING_APPROVAL, S.AWAITING_APPROVAL, R.APPROVAL_REQUIRED)

    async def _execute_granted(
        self,
        run: _Run,
        handler: ActionHandler,
        context: ActionExecutionContext,
        validated: BaseModel,
        approval_id: UUID,
    ) -> ActionRun:
        """Consume the granted request for THIS exact subject (once), then execute."""
        S, E = ActionRunStatus, AuditEventType
        actor, broker = run.request.actor, self._approvals
        status: ApprovalClaimStatus | None = None
        if isinstance(approval_id, UUID) and actor is not None and broker is not None:
            run.approval_id = approval_id
            claim = ApprovalClaim(
                approval_id=approval_id, company_id=run.scope.company_id,
                store_id=run.scope.store_id, action_name=context.action_name,
                requester_actor_id=actor.actor_id, action_run_id=run.run_id,
            )  # fmt: skip
            try:
                status = ApprovalClaimStatus(await broker.claim(claim, validated))
            except Exception:  # noqa: BLE001 - unknown: never execute
                status = None
        if status is not ApprovalClaimStatus.CLAIMED:
            reason = _CLAIM_REFUSALS.get(status, ActionRunReason.APPROVAL_UNAVAILABLE)
            return await run.finish(E.APPROVAL_REFUSED, S.FAILED, reason)
        outcome = await self._execute(run, handler, context, validated)
        if broker is not None:
            try:
                await broker.record_execution(
                    run.scope.company_id, approval_id, run.run_id, _OUTCOMES[outcome.status]
                )
            except Exception:  # noqa: BLE001, S110 - correlation never changes the run
                pass
        return outcome

    async def _execute(
        self,
        run: _Run,
        handler: ActionHandler,
        context: ActionExecutionContext,
        validated: BaseModel,
    ) -> ActionRun:
        """The single side-effect path: pre-execution audit, execute, verify, audit."""
        S, R, E = ActionRunStatus, ActionRunReason, AuditEventType
        # Audit before the side effect is mandatory: no record, no execution.
        if not await run.audit(E.EXECUTION_STARTED):
            return run.result(S.FAILED, R.AUDIT_UNAVAILABLE)

        try:
            execution = await handler.execute(context, validated)
            if not isinstance(execution, ExecutionResult):
                raise TypeError("handler returned an invalid execution result")
        except ActionExecutionError as exc:
            if not exc.effect_may_have_occurred:
                return await run.finish(E.EXECUTION_FAILED, S.FAILED, R.EXECUTION_FAILED_NO_EFFECT)
            return await self._uncertain(run, handler, context, validated)
        except Exception:  # noqa: BLE001 - unknown failure inside the side-effect boundary
            return await self._uncertain(run, handler, context, validated)

        await run.audit(E.EXECUTION_COMPLETED, execution_reference_id=execution.reference_id)
        await run.audit(E.VERIFICATION_STARTED, execution_reference_id=execution.reference_id)

        # Verification always runs after a completed execute, whatever the audit state.
        verification = await self._verify(handler, context, validated, execution)
        if verification is None:
            return await run.finish(
                E.REQUIRES_HUMAN, S.REQUIRES_HUMAN, R.VERIFICATION_ERROR, execution
            )

        if not verification.verified:
            return await run.finish(
                E.REQUIRES_HUMAN, S.REQUIRES_HUMAN, R.VERIFICATION_FAILED, execution, verification
            )
        if run.audit_complete:
            verified = await run.finish(E.VERIFIED, S.VERIFIED, R.VERIFIED, execution, verification)
            if verified.audit_complete:
                return verified
        # Verified, but the audit trail is incomplete: never report VERIFIED.
        return await run.finish(
            E.REQUIRES_HUMAN, S.REQUIRES_HUMAN, R.AUDIT_INCOMPLETE, execution, verification
        )

    @staticmethod
    def _validate(
        handler: ActionHandler, parameters: Mapping[str, Any]
    ) -> BaseModel | ActionRunReason:
        """Raw parameters -> trusted immutable input, or a failure reason. No side effects."""
        try:
            raw = MappingProxyType(dict(parameters))
            validated = handler.validate(raw)
        except Exception:  # noqa: BLE001 - never echo raw values; the reason is enough
            return ActionRunReason.INPUT_INVALID
        if not isinstance(validated, BaseModel) or not validated.model_config.get("frozen"):
            return ActionRunReason.HANDLER_CONTRACT_VIOLATION
        return validated

    @staticmethod
    async def _verify(
        handler: ActionHandler,
        context: ActionExecutionContext,
        validated: BaseModel,
        execution: ExecutionResult | None,
    ) -> VerificationResult | None:
        """Call the handler's independent verifier; None if it raised or broke contract."""
        try:
            verification = await handler.verify(context, validated, execution)
        except Exception:  # noqa: BLE001 - never leak verifier errors; None means unverified
            return None
        return verification if isinstance(verification, VerificationResult) else None

    async def _uncertain(
        self,
        run: _Run,
        handler: ActionHandler,
        context: ActionExecutionContext,
        validated: BaseModel,
    ) -> ActionRun:
        """Execution may have had an effect but produced no trustworthy receipt.

        Verification is still attempted (with no ExecutionResult) so a human gets
        recovery evidence. Audit failures here never skip it, and its answer never
        upgrades the run: the outcome stays REQUIRES_HUMAN / execution_outcome_uncertain.
        """
        S, reason = ActionRunStatus, ActionRunReason.EXECUTION_OUTCOME_UNCERTAIN
        await run.audit(
            AuditEventType.EXECUTION_FAILED, run_status=S.REQUIRES_HUMAN, run_reason=reason
        )
        await run.audit(AuditEventType.VERIFICATION_STARTED)
        verification = await self._verify(handler, context, validated, None)
        return await run.finish(
            AuditEventType.REQUIRES_HUMAN, S.REQUIRES_HUMAN, reason, verification=verification
        )
