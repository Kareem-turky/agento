"""Employee Chat domain models (Task 042).

Employee Chat is the authenticated COMPANY EMPLOYEE's private, durable conversation
with the Product's Operations Agent. It is NOT the external Conversation transcript of
Task 037 (``app.conversations``: provider / channel / customer messages).

Every identity and scope value here (company, actor id and type, store) is derived by
the Product from the authenticated ``ActorContext``. The client controls only the store
selector of a new thread (checked against the actor's granted stores), its own
idempotent turn id and the message text. The Agent is always ``operations``.
"""

import re
from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Self
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, StringConstraints, model_validator

from app.context.models import ActorType

_FROZEN = ConfigDict(frozen=True, extra="forbid")

CHAT_AGENT_ID = "operations"
TICKET_PROPOSAL_ACTION = "operations.ticket.create"

MAX_MESSAGE_CHARS = 8000
# The stored assistant answer is bounded; a longer model answer is cut deterministically.
MAX_ASSISTANT_CHARS = 16000
MAX_TICKET_TITLE_CHARS = 160
MAX_TICKET_DESCRIPTION_CHARS = 4000

# Bounded multi-turn context handed to the Operations chat runner (see history.py).
HISTORY_MAX_TURNS = 12
HISTORY_MAX_CHARS = 24000

MAX_THREADS_LISTED = 50
MAX_TURNS_LISTED = 200

ScopedId = Annotated[str, StringConstraints(strict=True, min_length=1, max_length=256)]
TicketTitle = Annotated[
    str, StringConstraints(strict=True, strip_whitespace=True, min_length=1,
                           max_length=MAX_TICKET_TITLE_CHARS)
]  # fmt: skip
TicketDescription = Annotated[
    str, StringConstraints(strict=True, strip_whitespace=True, min_length=1,
                           max_length=MAX_TICKET_DESCRIPTION_CHARS)
]  # fmt: skip
_KEY_HASH = re.compile(r"^[0-9a-f]{64}$")


class TurnStatus(StrEnum):
    PENDING = "pending"
    COMPLETED = "completed"
    FAILED = "failed"


class TurnFailure(StrEnum):
    """Why a turn has no answer (stable, value-free codes)."""

    AGENT_DISABLED = "agent_disabled"
    RUN_FAILED = "run_failed"


class ProposalState(StrEnum):
    """PROPOSED: shown to the employee, nothing executed. CONFIRMING: an explicit human
    confirmation claimed it with one Idempotency-Key (the WriteCommand may be in flight;
    only the SAME key may continue). SUBMITTED: the durable WriteCommand exists
    (``command_id``). CANCELLED: terminal, never executes."""

    PROPOSED = "proposed"
    CONFIRMING = "confirming"
    SUBMITTED = "submitted"
    CANCELLED = "cancelled"


class ChatThread(BaseModel):
    model_config = _FROZEN

    thread_id: UUID
    company_id: ScopedId
    actor_id: ScopedId
    actor_type: ActorType
    store_id: ScopedId
    agent_id: str
    created_at: AwareDatetime
    updated_at: AwareDatetime
    next_turn_sequence: int

    @model_validator(mode="after")
    def _valid(self) -> Self:
        if self.agent_id != CHAT_AGENT_ID:
            raise ValueError("employee chat threads belong to the Operations Agent")
        if self.next_turn_sequence < 1:
            raise ValueError("turn sequence starts at 1")
        return self


class ChatTurn(BaseModel):
    model_config = _FROZEN

    turn_id: UUID
    thread_id: UUID
    company_id: ScopedId
    sequence: int
    user_text: Annotated[str, StringConstraints(min_length=1, max_length=MAX_MESSAGE_CHARS)]
    assistant_text: Annotated[str, StringConstraints(max_length=MAX_ASSISTANT_CHARS)] | None
    status: TurnStatus
    failure: TurnFailure | None
    created_at: AwareDatetime
    completed_at: AwareDatetime | None

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if self.sequence < 1:
            raise ValueError("turn sequence starts at 1")
        completed = self.status is TurnStatus.COMPLETED
        if completed != (self.assistant_text is not None):
            raise ValueError("only a completed turn has an assistant answer")
        if (self.status is TurnStatus.FAILED) != (self.failure is not None):
            raise ValueError("only a failed turn has a failure code")
        if (self.status is TurnStatus.PENDING) != (self.completed_at is None):
            raise ValueError("a finished turn has a completion time")
        return self


class ProposedTicket(BaseModel):
    """What the chat runner may return: a TYPED, UNTRUSTED ticket proposal (model text).
    It is never authorization and never executed by itself."""

    model_config = _FROZEN

    title: TicketTitle
    description: TicketDescription


class TicketProposal(BaseModel):
    model_config = _FROZEN

    proposal_id: UUID
    thread_id: UUID
    turn_id: UUID
    company_id: ScopedId
    action_name: str
    title: TicketTitle
    description: TicketDescription
    state: ProposalState
    confirm_key_hash: str | None
    command_id: UUID | None
    created_at: AwareDatetime
    updated_at: AwareDatetime

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if self.action_name != TICKET_PROPOSAL_ACTION:
            raise ValueError("the only chat proposal is operations.ticket.create")
        if self.confirm_key_hash is not None and not _KEY_HASH.fullmatch(self.confirm_key_hash):
            raise ValueError("confirm_key_hash is a SHA-256 hex digest")
        claimed = self.state in (ProposalState.CONFIRMING, ProposalState.SUBMITTED)
        if claimed != (self.confirm_key_hash is not None):
            raise ValueError("a claimed proposal (and only one) carries its key hash")
        if (self.state is ProposalState.SUBMITTED) != (self.command_id is not None):
            raise ValueError("a submitted proposal (and only one) carries its command id")
        return self


class HistoryTurn(BaseModel):
    """One prior completed exchange handed to the runner as CONTEXT only: never identity,
    permissions, company, store or policy."""

    model_config = _FROZEN

    user_text: str
    assistant_text: str


class ChatRunResult(BaseModel):
    """The chat runner's answer: the final assistant text (untrusted display text) and at
    most one typed ticket proposal."""

    model_config = _FROZEN

    message: str
    proposal: ProposedTicket | None = None


def utc_now() -> datetime:
    return datetime.now(UTC)
