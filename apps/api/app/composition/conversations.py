"""Product Conversation composition (Task 037), independent of the business backend.

    settings -> (database configured?)  no  -> nothing (Conversation routes answer 503)
             -> engine + sessions -> PostgresConversationRepository
                                   + PostgresIntegrationConnectionRepository (the EXISTING
                                     Task 031 connection metadata, read only: no second
                                     connection, configuration or secret system)
             -> IntegrationCatalog (this build: build_default_integration_catalog(), EMPTY)
             -> MessagingIntegrationRegistry (validated against it; EMPTY in this build)
             -> GovernanceGate(CONVERSATION_ACTIONS) -> ConversationReadService
             -> ConversationIngress / ConversationDelivery: the canonical seams a future,
                reviewed provider adapter will call. NOTHING in this build calls them:
                there is no public webhook, ingest or send route, no worker and no poller.

Observed through the ONE Product observability given by the composition root (never
built here). No secret store is read and no network call is made. Nothing here migrates
or creates tables.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.composition.contracts import DeploymentCompositionError
from app.config import Settings
from app.observability.contracts import ProductObservability

if TYPE_CHECKING:
    from app.conversations.ingress import ConversationDelivery, ConversationIngress
    from app.conversations.service import ConversationReadService
    from app.integration_management import IntegrationCatalog
    from app.integrations.messaging import MessagingIntegrationRegistry

RELEASE_FAILED = "conversation resources could not be released"


async def _nothing() -> None:
    return None


@dataclass(frozen=True)
class ConversationComposition:
    service: "ConversationReadService | None" = None
    ingress: "ConversationIngress | None" = None
    delivery: "ConversationDelivery | None" = None
    messaging: "MessagingIntegrationRegistry | None" = None
    close: Callable[[], Awaitable[None]] = _nothing
    discard: Callable[[], None] = lambda: None


def build_conversations(
    settings: Settings,
    *,
    observability: ProductObservability | None = None,
    catalog: "IntegrationCatalog | None" = None,
) -> ConversationComposition:
    if settings.database_url is None:
        return ConversationComposition()
    # Imported only here (called after the business backend was allowed).
    from app.conversations.ingress import ConversationDelivery, ConversationIngress
    from app.conversations.permissions import CONVERSATION_ACTIONS
    from app.conversations.service import ConversationReadService
    from app.governance import ActionCatalog, GovernanceGate
    from app.integration_management import build_default_integration_catalog
    from app.integrations.messaging import build_default_messaging_registry
    from app.persistence import (
        PostgresConversationRepository,
        PostgresIntegrationConnectionRepository,
        create_product_engine,
        create_session_factory,
    )

    installed = catalog if catalog is not None else build_default_integration_catalog()
    # Validated against the installed integrations before any resource (EMPTY here).
    messaging = build_default_messaging_registry(installed)
    engine = create_product_engine(str(settings.database_url))
    released = False

    async def close() -> None:
        nonlocal released
        if released:
            return
        released = True
        try:
            await engine.dispose()
        except Exception:  # noqa: BLE001 - never surface driver/URL details
            raise DeploymentCompositionError(RELEASE_FAILED) from None

    def discard() -> None:
        nonlocal released
        if released:
            return
        released = True
        try:
            engine.sync_engine.dispose()
        except Exception:  # noqa: BLE001 - never surface driver/URL details
            raise DeploymentCompositionError(RELEASE_FAILED) from None

    try:
        sessions = create_session_factory(engine)
        repository = PostgresConversationRepository(sessions)
        connections = PostgresIntegrationConnectionRepository(sessions)
        service = ConversationReadService(
            repository, GovernanceGate(ActionCatalog(CONVERSATION_ACTIONS)),
            connections=connections, catalog=installed, observability=observability,
        )  # fmt: skip
        ingress = ConversationIngress(repository, connections, installed,
                                      observability=observability)  # fmt: skip
        delivery = ConversationDelivery(repository, observability=observability)
        return ConversationComposition(service=service, ingress=ingress, delivery=delivery,
                                       messaging=messaging, close=close,
                                       discard=discard)  # fmt: skip
    except BaseException:
        discard()
        raise
