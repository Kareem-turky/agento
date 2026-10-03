"""Provider-independent delivery states and the outbound transition graph (Task 037).

    pending  -> accepted | failed | unknown
    accepted -> sent | delivered | failed | unknown
    sent     -> delivered | failed | unknown
    unknown  -> accepted | sent | delivered | failed
    delivered, failed: terminal (v1)

``received`` belongs to inbound messages only and never transitions. An update that is
not a forward transition from the CURRENT state (a late ``sent`` after ``delivered``, a
repeat of the current state, anything after a terminal state) is STALE: it never changes
the message's current state (which therefore never regresses).

Append-only ``MessageDeliveryEvent`` rows are the durable delivery-OBSERVATION history:

- an APPLIED forward transition is always recorded (``applied=True``);
- a STALE update that carries a provider ``external_event_ref`` is ALSO recorded
  (``applied=False``), so that provider event identity is remembered: replaying it with
  the same canonical data is a DUPLICATE, with different data a conflict (fail closed);
- a STALE update without an ``external_event_ref`` has no identity to remember and is
  not recorded.

``MessageDeliveryEvent.state`` is the canonical state REPORTED by that observation, not
necessarily the message's state: ``sent`` (applied=False) after ``delivered`` leaves the
message ``delivered``.
"""

from enum import StrEnum
from types import MappingProxyType
from typing import Self
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from app.conversations.models import DeliveryState, ExternalRef, ScopedId

D = DeliveryState
TRANSITIONS = MappingProxyType({
    D.PENDING: frozenset({D.ACCEPTED, D.FAILED, D.UNKNOWN}),
    D.ACCEPTED: frozenset({D.SENT, D.DELIVERED, D.FAILED, D.UNKNOWN}),
    D.SENT: frozenset({D.DELIVERED, D.FAILED, D.UNKNOWN}),
    D.UNKNOWN: frozenset({D.ACCEPTED, D.SENT, D.DELIVERED, D.FAILED}),
    D.DELIVERED: frozenset(),
    D.FAILED: frozenset(),
    D.RECEIVED: frozenset(),
})  # fmt: skip
OUTBOUND_STATES = frozenset(TRANSITIONS) - {D.RECEIVED}
UPDATE_STATES = frozenset({D.ACCEPTED, D.SENT, D.DELIVERED, D.FAILED, D.UNKNOWN})


def is_transition(current: DeliveryState, new: DeliveryState) -> bool:
    return DeliveryState(new) in TRANSITIONS[DeliveryState(current)]


class DeliveryUpdate(BaseModel):
    """A canonical delivery report (from a future provider adapter)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    state: DeliveryState
    occurred_at: AwareDatetime
    external_event_ref: ExternalRef | None = None


class MessageDeliveryEvent(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    message_id: UUID
    company_id: ScopedId
    sequence: int = Field(ge=1)
    state: DeliveryState
    occurred_at: AwareDatetime
    recorded_at: AwareDatetime
    external_event_ref: ExternalRef | None = None
    applied: bool  # True: this observation changed the message's current state

    @model_validator(mode="after")
    def _identified_if_not_applied(self) -> Self:
        if not self.applied and self.external_event_ref is None:
            raise ValueError("an unapplied observation is recorded only for an event ref")
        return self


class DeliveryOutcome(StrEnum):
    APPLIED = "applied"  # a forward transition: state changed, one event recorded
    DUPLICATE = "duplicate"  # the same external event again: nothing changed
    STALE = "stale"  # not a forward transition: state unchanged (observation kept if ref'd)


class DeliveryResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    outcome: DeliveryOutcome
    state: DeliveryState  # the CURRENT canonical state after the update
