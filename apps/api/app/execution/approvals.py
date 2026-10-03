"""The human-approval port of governed execution (Task 036).

``ExecutionCoordinator`` depends only on this contract; the Product implementation is
``app.approval_management`` (durable requests, decisions, expiry, one-time consumption).

* ``ApprovalSummary`` / ``ApprovalChange``: the SAFE, bounded description a human reads
  before deciding (title, description, before/after values). It is produced explicitly
  by trusted handler code (``ApprovalDescriber``), never inferred from raw parameters,
  and it is never what an approval is bound to: execution binds to the exact subject
  fingerprint the broker derives from the VALIDATED input.
* ``ApprovalBroker``: records a request for a governed action that policy says needs a
  human decision, and later CLAIMS a granted request exactly once for the exact same
  subject (action, requester, company, store, validated input). Raw parameters never
  cross this port: only the frozen validated input, from which the broker derives a
  one-way fingerprint.

An approval is an ADDITIONAL condition: the coordinator re-evaluates governance (current
permission and policy) before any claim, so an approval never replaces a permission.
"""

from enum import StrEnum
from typing import Annotated, Protocol, runtime_checkable
from uuid import UUID

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StringConstraints

from app.context.models import ActorType
from app.execution.context import ActionExecutionContext
from app.governance import ActionRisk

_FROZEN = ConfigDict(frozen=True, extra="forbid")

MAX_SUMMARY_CHANGES = 10


def _printable(value: str) -> str:
    if any(ord(ch) < 32 and ch not in "\n\t" or ord(ch) == 127 for ch in value):
        raise ValueError("control characters are not allowed")
    if not value.strip():
        raise ValueError("blank text is not allowed")
    return value


SafeText = Annotated[str, StringConstraints(strict=True, max_length=500),
                     AfterValidator(_printable)]  # fmt: skip
ShortText = Annotated[str, StringConstraints(strict=True, max_length=120),
                      AfterValidator(_printable)]  # fmt: skip
DisplayValue = Annotated[str, StringConstraints(strict=True, max_length=200),
                         AfterValidator(_printable)]  # fmt: skip
ChangeCode = Annotated[str, StringConstraints(strict=True, pattern=r"^[a-z][a-z0-9_.]{0,63}$")]
SourceId = Annotated[str, StringConstraints(strict=True, pattern=r"^[a-z][a-z0-9_.]{0,127}$")]


class ApprovalChange(BaseModel):
    """One safe before/after line (display only; ``None`` means "not set")."""

    model_config = _FROZEN

    code: ChangeCode
    label: ShortText
    before: DisplayValue | None = None
    after: DisplayValue | None = None


class ApprovalSummary(BaseModel):
    """What a human approver sees. Bounded, trusted, never authority."""

    model_config = _FROZEN

    title: ShortText
    description: SafeText
    changes: tuple[ApprovalChange, ...] = Field(default=(), max_length=MAX_SUMMARY_CHANGES)


@runtime_checkable
class ApprovalDescriber(Protocol):
    """Implemented by an ActionHandler whose action may require a human decision.

    Deterministic and side-effect free: no network, model, provider call or secret.
    A handler without it cannot be approved: execution fails closed."""

    def describe_approval(
        self, context: ActionExecutionContext, validated_input: BaseModel
    ) -> ApprovalSummary: ...


class ApprovalSource(StrEnum):
    ACTION = "action"
    WRITE_COMMAND = "write_command"
    WORKFLOW_STEP = "workflow_step"


class ApprovalSourceRef(BaseModel):
    """Trusted correlation metadata of where a request came from (grants nothing)."""

    model_config = _FROZEN

    kind: ApprovalSource = ApprovalSource.ACTION
    command_id: UUID | None = None
    workflow_run_id: UUID | None = None
    workflow_id: SourceId | None = None
    workflow_step_id: SourceId | None = None


class ApprovalSubject(BaseModel):
    """A request for a human decision on ONE exact governed action subject."""

    model_config = _FROZEN

    company_id: str
    store_id: str | None
    action_name: str
    risk: ActionRisk
    requester_actor_id: str
    requester_actor_type: ActorType
    request_id: UUID
    action_run_id: UUID
    summary: ApprovalSummary
    source: ApprovalSourceRef


class ApprovalClaim(BaseModel):
    """Consume a granted request for this exact subject, once."""

    model_config = _FROZEN

    approval_id: UUID
    company_id: str
    store_id: str | None
    action_name: str
    requester_actor_id: str
    requester_actor_type: ActorType  # the exact principal: id AND type
    action_run_id: UUID


class ApprovalClaimStatus(StrEnum):
    CLAIMED = "claimed"  # this run is the one execution the request allows
    NOT_FOUND = "not_found"  # unknown here (another company's id is indistinguishable)
    NOT_DECIDED = "not_decided"  # still awaiting a human decision
    REJECTED = "rejected"
    EXPIRED = "expired"
    CANCELLED = "cancelled"
    MISMATCH = "mismatch"  # another action, requester principal, store or input
    ALREADY_CONSUMED = "already_consumed"


class ApprovalOutcome(StrEnum):
    """The execution outcome a consumed request is correlated with (metadata only)."""

    VERIFIED = "verified"
    FAILED = "failed"
    REQUIRES_HUMAN = "requires_human"


@runtime_checkable
class ApprovalContinuationGuard(Protocol):
    """A NON-CONSUMING precheck protecting durable WriteCommand state (Task 036).

    Answers only whether ``approval_id`` is a request of this company, made by this exact
    requester principal (actor id AND actor type), for the WRITE_COMMAND source with this
    exact ``command_id``. Unknown, foreign and mismatched requests are the same ``False``.
    It never consumes, decides, changes or exposes a request, and it authorizes nothing:
    execution still goes through ``ExecutionCoordinator`` (governance re-check, exact
    subject fingerprint, status/expiry, one-time claim). Raises when it cannot answer:
    the caller then fails closed."""

    async def is_command_requester(
        self, company_id: str, approval_id: UUID, *, requester_actor_id: str,
        requester_actor_type: str, command_id: UUID,
    ) -> bool: ...  # fmt: skip


@runtime_checkable
class ApprovalBroker(Protocol):
    async def request(self, subject: ApprovalSubject, validated_input: BaseModel) -> UUID:
        """Durably record the request (state + its first event, atomically) and return
        its id. Raises when it cannot: the caller then fails closed."""
        ...

    async def claim(self, claim: ApprovalClaim, validated_input: BaseModel) -> ApprovalClaimStatus:
        """Atomically consume a granted, unexpired, unconsumed request whose subject
        (action, requester, company, store, validated input) is exactly this one."""
        ...

    async def record_execution(
        self, company_id: str, approval_id: UUID, action_run_id: UUID, outcome: ApprovalOutcome
    ) -> None:
        """Correlate the consumed request with its execution outcome (best effort)."""
        ...
