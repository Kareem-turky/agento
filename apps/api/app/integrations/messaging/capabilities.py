"""Stable, provider-independent messaging capability ids (Task 037).

They use the dotted vocabulary of ``IntegrationDefinition.capabilities`` (for example
``orders.read``). A capability is DESCRIPTIVE metadata about what an installed
integration can do; it is never a permission and never grants anything.
"""

from enum import StrEnum


class MessagingCapability(StrEnum):
    RECEIVE = "messages.receive"  # inbound messages can be ingested from this channel
    SEND = "messages.send"  # outbound text messages can be sent (future governed action)
    DELIVERY = "messages.delivery"  # outbound delivery-state updates are reported


MESSAGING_CAPABILITIES = frozenset(c.value for c in MessagingCapability)
