"""Provider-independent messaging integration contract (Task 037).

A future messaging provider installs its CONNECTION management through the existing
Integration Foundation (``IntegrationDefinition`` with category MESSAGING plus a
connection driver in the ``IntegrationCatalog``) and implements the BUSINESS operations
of this package (``MessagingIntegration``) separately. No provider is installed in this
build: the production ``MessagingIntegrationRegistry`` is empty.
"""

from app.integrations.messaging.capabilities import (
    MESSAGING_CAPABILITIES,
    MessagingCapability,
)
from app.integrations.messaging.contract import (
    MAX_OUTBOUND_TEXT_CHARS,
    MessagingIntegration,
    OutboundMessageRequest,
    OutboundMessageResult,
    OutboundSendStatus,
)
from app.integrations.messaging.errors import (
    MessagingIntegrationError,
    MessagingRegistryError,
    MessagingSendRejectedError,
    MessagingSendUncertainError,
    MessagingUnavailableError,
)
from app.integrations.messaging.registry import (
    MessagingIntegrationRegistry,
    build_default_messaging_registry,
)

__all__ = [
    "MAX_OUTBOUND_TEXT_CHARS",
    "MESSAGING_CAPABILITIES",
    "MessagingCapability",
    "MessagingIntegration",
    "MessagingIntegrationError",
    "MessagingIntegrationRegistry",
    "MessagingRegistryError",
    "MessagingSendRejectedError",
    "MessagingSendUncertainError",
    "MessagingUnavailableError",
    "OutboundMessageRequest",
    "OutboundMessageResult",
    "OutboundSendStatus",
    "build_default_messaging_registry",
]
