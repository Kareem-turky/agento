"""``MessagingIntegration``: the Product-owned messaging business contract (Task 037).

    Integration Management (Task 031)   connection metadata, credentials, enable/disable,
                                        connectivity tests (``IntegrationConnectionDriver``)
    Messaging Integration (this file)   the actual messaging operations

A provider adapter may implement both, but they stay separate Product contracts. The
contract takes and returns CANONICAL data only: never a provider payload, header,
webhook body, token or provider error object.

Inbound messages do not flow through this contract: a future provider-specific ingress
route authenticates the provider (its own signature rules), maps the event to a
canonical ``InboundMessageEnvelope`` and calls the Product ``ConversationIngress``.

Outbound (``send_message``) is the FUTURE seam: this build exposes no send action, route,
tool or Agent. When a governed send action is added it will call this method once and
map the outcome:

- ``OutboundMessageResult``           the provider accepted (or sent) the message;
- ``MessagingSendRejectedError``      definitively not sent;
- ``MessagingUnavailableError``       not sent (could not reach the provider);
- ``MessagingSendUncertainError``     UNKNOWN: it may have been sent. An uncertain
                                      write is NEVER retried blindly; it stays
                                      ``unknown`` until a delivery update or a human
                                      resolves it (the governed-write rule of Task 013).
"""

from enum import StrEnum
from typing import Annotated, Protocol, runtime_checkable
from uuid import UUID

from pydantic import AfterValidator, AwareDatetime, BaseModel, ConfigDict, StringConstraints

MAX_OUTBOUND_TEXT_CHARS = 16_000
_FROZEN = ConfigDict(frozen=True, extra="forbid")


def _printable(value: str) -> str:
    if any((ord(ch) < 32 and ch not in "\n\t") or ord(ch) == 127 for ch in value):
        raise ValueError("control characters are not allowed")
    if not value.strip():
        raise ValueError("blank text is not allowed")
    return value


# An opaque external reference: bounded, printable, case-sensitive and never interpreted.
ExternalRef = Annotated[
    str, StringConstraints(strict=True, min_length=1, max_length=256, pattern=r"^[\x21-\x7e]+$")
]
OutboundText = Annotated[
    str,
    StringConstraints(strict=True, min_length=1, max_length=MAX_OUTBOUND_TEXT_CHARS),
    AfterValidator(_printable),
]


class OutboundMessageRequest(BaseModel):
    """One canonical plain-text message to an existing external conversation."""

    model_config = _FROZEN

    connection_id: UUID
    external_conversation_ref: ExternalRef
    message_id: UUID  # the Product message id (an idempotency hint for the provider)
    text: OutboundText


class OutboundSendStatus(StrEnum):
    ACCEPTED = "accepted"
    SENT = "sent"


class OutboundMessageResult(BaseModel):
    model_config = _FROZEN

    status: OutboundSendStatus
    external_message_ref: ExternalRef | None = None
    occurred_at: AwareDatetime


@runtime_checkable
class MessagingIntegration(Protocol):
    @property
    def integration_id(self) -> str:
        """The Product Integration id of its ``IntegrationDefinition`` (must match)."""
        ...

    @property
    def capabilities(self) -> frozenset[str]:
        """The messaging capabilities this adapter implements (descriptive only)."""
        ...

    async def send_message(self, request: OutboundMessageRequest) -> OutboundMessageResult:
        """Send ONE canonical text message, at most once per call (see module doc)."""
        ...
