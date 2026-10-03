"""``ConversationIngress``: the ONLY way an inbound external message enters the Product
(Task 037), and ``ConversationDelivery``: canonical delivery updates.

    trusted ChannelContext (company_id, connection_id[, store_id])   <- Product code
    + canonical InboundMessageEnvelope                               <- provider adapter
      -> IntegrationConnection of THIS company?  enabled?            (Task 031 metadata)
      -> installed in the IntegrationCatalog? category MESSAGING? declares messages.receive?
      -> SHA-256 inbound fingerprint
      -> ConversationRepository.ingest: ONE transaction (conversation, deduplicated
         message with the next sequence, last-message fields)

Company, store and connection never come from the external payload. There is no public
ingest route: a future provider adapter exposes its own reviewed, provider-authenticated
route and calls this synchronously (no queue, worker or poller). Message text is
UNTRUSTED EXTERNAL DATA: it is stored as text and nothing here interprets it, logs it,
audits it, gives it to an Agent or a model, or triggers anything from it. This module
never reads integration secrets and makes no network call.
"""

from collections.abc import Callable
from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import ValidationError

from app.conversations.contracts import ConversationRepository, IngestResult, ResolvedChannel
from app.conversations.delivery import UPDATE_STATES, DeliveryResult, DeliveryUpdate
from app.conversations.errors import (
    ConversationRepositoryError,
    ConversationStoreConflictError,
    ConversationUnavailableError,
    DeliveryEventConflictError,
    DeliveryEventFingerprintConflictError,
    DeliveryRefusal,
    DeliveryUpdateRefusedError,
    InboundMessageConflictError,
    InboundMessageRefusedError,
    IngressRefusal,
    MessageFingerprintConflictError,
)
from app.conversations.models import ChannelContext, InboundMessageEnvelope, inbound_fingerprint
from app.integration_management import (
    IntegrationCatalog,
    IntegrationCategory,
    IntegrationConnectionRepository,
)
from app.integration_management.connections import ConnectionRepositoryError
from app.integrations.messaging.capabilities import MessagingCapability
from app.observability.contracts import (
    BusinessDetails,
    ObservationDetails,
    ObservationOutcome,
    ProductObservability,
    ProductOperation,
    observe,
)


def _utc_now() -> datetime:
    return datetime.now(UTC)


class IngestOutcome(StrEnum):
    """Low-cardinality observability label of one inbound message."""

    RECORDED = "recorded"
    REPLAYED = "replayed"
    REFUSED = "refused"
    CONFLICT = "conflict"
    UNAVAILABLE = "unavailable"


def _observe(obs, outcome: ObservationOutcome, status: StrEnum, reason: StrEnum | None = None):
    obs.finish(outcome, ObservationDetails(business=BusinessDetails(status=status,
                                                                    reason=reason)))  # fmt: skip


class ConversationIngress:
    def __init__(
        self,
        repository: ConversationRepository,
        connections: IntegrationConnectionRepository,
        catalog: IntegrationCatalog,
        *,
        clock: Callable[[], datetime] = _utc_now,
        new_id: Callable[[], UUID] = uuid4,
        observability: ProductObservability | None = None,
    ) -> None:
        self._repository = repository
        self._connections = connections
        self._catalog = catalog
        self._clock = clock
        self._new_id = new_id
        self._observability = observability

    async def _channel(self, context: ChannelContext) -> ResolvedChannel:
        try:
            connection = await self._connections.get(context.company_id, context.connection_id)
        except ConnectionRepositoryError:
            raise ConversationUnavailableError() from None
        if connection is None or connection.company_id != context.company_id:
            raise InboundMessageRefusedError(IngressRefusal.CONNECTION_NOT_FOUND)
        if connection.enabled is not True:
            raise InboundMessageRefusedError(IngressRefusal.CONNECTION_DISABLED)
        installed = self._catalog.get(connection.integration_id)
        if installed is None:
            raise InboundMessageRefusedError(IngressRefusal.INTEGRATION_NOT_INSTALLED)
        definition = installed.definition
        if definition.category is not IntegrationCategory.MESSAGING:
            raise InboundMessageRefusedError(IngressRefusal.NOT_MESSAGING)
        if MessagingCapability.RECEIVE.value not in definition.capabilities:
            raise InboundMessageRefusedError(IngressRefusal.RECEIVE_NOT_SUPPORTED)
        return ResolvedChannel(company_id=context.company_id, store_id=context.store_id,
                               connection_id=connection.connection_id,
                               integration_id=definition.integration_id)  # fmt: skip

    async def ingest(self, context: ChannelContext, envelope: object) -> IngestResult:
        """Record one inbound message (or return the identical one already recorded)."""
        with observe(self._observability, ProductOperation.CONVERSATION_INGEST) as obs:
            try:
                if not isinstance(context, ChannelContext):
                    raise TypeError("a trusted ChannelContext is required")
                if not isinstance(envelope, InboundMessageEnvelope):
                    try:  # a mapping from an adapter: validated strictly, never echoed
                        envelope = InboundMessageEnvelope.model_validate(envelope)
                    except ValidationError:
                        raise InboundMessageRefusedError(IngressRefusal.ENVELOPE_INVALID) from None
                channel = await self._channel(context)
                now = self._clock()
                if not isinstance(now, datetime) or now.utcoffset() is None:
                    raise ConversationUnavailableError()
                result = await self._repository.ingest(
                    channel, envelope,
                    fingerprint=inbound_fingerprint(channel.connection_id, envelope),
                    new_conversation_id=self._new_id(), new_message_id=self._new_id(), now=now,
                )  # fmt: skip
            except InboundMessageRefusedError as error:
                _observe(obs, ObservationOutcome.INVALID, IngestOutcome.REFUSED, error.reason)
                raise
            except MessageFingerprintConflictError:
                _observe(obs, ObservationOutcome.CONFLICT, IngestOutcome.CONFLICT)
                raise InboundMessageConflictError() from None
            except ConversationStoreConflictError:
                _observe(obs, ObservationOutcome.CONFLICT, IngestOutcome.REFUSED,
                         IngressRefusal.STORE_CONFLICT)  # fmt: skip
                raise InboundMessageRefusedError(IngressRefusal.STORE_CONFLICT) from None
            except (ConversationRepositoryError, ConversationUnavailableError):
                _observe(obs, ObservationOutcome.UNAVAILABLE, IngestOutcome.UNAVAILABLE)
                raise ConversationUnavailableError() from None
            _observe(
                obs,
                ObservationOutcome.COMPLETED,
                IngestOutcome.REPLAYED if result.replayed else IngestOutcome.RECORDED,
            )
            return result


class ConversationDelivery:
    """Canonical delivery-state updates of OUTBOUND messages (the future provider
    delivery seam; no route or caller in this build)."""

    def __init__(
        self,
        repository: ConversationRepository,
        *,
        clock: Callable[[], datetime] = _utc_now,
        observability: ProductObservability | None = None,
    ) -> None:
        self._repository = repository
        self._clock = clock
        self._observability = observability

    async def record(self, company_id: str, message_id: UUID, update: object) -> DeliveryResult:
        with observe(self._observability, ProductOperation.CONVERSATION_DELIVERY) as obs:
            try:
                if not isinstance(update, DeliveryUpdate):
                    try:
                        update = DeliveryUpdate.model_validate(update)
                    except ValidationError:
                        raise DeliveryUpdateRefusedError(DeliveryRefusal.INVALID_STATE) from None
                if update.state not in UPDATE_STATES:
                    raise DeliveryUpdateRefusedError(DeliveryRefusal.INVALID_STATE)
                if not isinstance(message_id, UUID):
                    raise DeliveryUpdateRefusedError(DeliveryRefusal.MESSAGE_NOT_FOUND)
                now = self._clock()
                result = await self._repository.record_delivery(company_id, message_id,
                                                                update, now)  # fmt: skip
            except DeliveryUpdateRefusedError as error:
                _observe(obs, ObservationOutcome.INVALID, error.reason)
                raise
            except DeliveryEventFingerprintConflictError:
                _observe(obs, ObservationOutcome.CONFLICT, IngestOutcome.CONFLICT)
                raise DeliveryEventConflictError() from None
            except ConversationRepositoryError:
                _observe(obs, ObservationOutcome.UNAVAILABLE, IngestOutcome.UNAVAILABLE)
                raise ConversationUnavailableError() from None
            _observe(obs, ObservationOutcome.COMPLETED, result.outcome, result.state)
            return result
