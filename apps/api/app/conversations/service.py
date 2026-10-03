"""``ConversationReadService``: Product Conversation inspection (Task 037).

    trusted RequestContext -> GovernanceGate (conversations.read) -> 403
      -> list:     this company's conversations, store-less or in the actor's stores
                   (filtered IN SQL), newest activity first, bounded
      -> get:      one conversation of this company the actor may see (else 404: an
                   unknown id, another company's and an inaccessible store's are
                   indistinguishable)
      -> messages: bounded keyset page by Product ``sequence`` (``before_sequence``)

Read-only: nothing here sends, replies, assigns, closes or ingests. Message text is
returned as data to an authorized reader and is never interpreted. Channel labels (the
integration's display name and the connection's display name) come from trusted Product
integration metadata; a deleted connection only loses its label, never its history.
Observability records fixed operation names and enum statuses only.
"""

from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

from app.context.models import ActorContext, RequestContext
from app.conversations.contracts import ConversationRepository
from app.conversations.errors import (
    ConversationAccessDeniedError,
    ConversationError,
    ConversationInputError,
    ConversationInputReason,
    ConversationNotFoundError,
    ConversationRepositoryError,
    ConversationUnavailableError,
)
from app.conversations.models import Conversation, ConversationMessage
from app.conversations.permissions import CONVERSATIONS_READ
from app.governance import ActionIntent, ActionScope, GovernanceGate, PolicyOutcome
from app.integration_management import IntegrationCatalog, IntegrationConnectionRepository
from app.integration_management.connections import ConnectionRepositoryError
from app.observability.contracts import (
    BusinessDetails,
    ObservationDetails,
    ObservationOutcome,
    ProductObservability,
    ProductOperation,
    observe,
)

MAX_CONVERSATIONS = 100
DEFAULT_CONVERSATIONS = 50
MAX_MESSAGES = 100
DEFAULT_MESSAGES = 50


class ConversationRead(StrEnum):
    """Low-cardinality observability label of a read."""

    LIST = "list"
    GET = "get"
    MESSAGES = "messages"


@dataclass(frozen=True, slots=True)
class ChannelLabel:
    """Safe, human-readable channel metadata (trusted Product data, never payload)."""

    integration_name: str | None  # None: the integration is not installed in this build
    connection_name: str | None  # None: the connection no longer exists


@dataclass(frozen=True, slots=True)
class ConversationSummary:
    conversation: Conversation
    channel: ChannelLabel


@dataclass(frozen=True, slots=True)
class MessagePage:
    messages: tuple[ConversationMessage, ...]  # ascending sequence
    next_before_sequence: int | None  # pass as ``before_sequence`` for older messages


def _outcome(error: BaseException) -> ObservationOutcome:
    if isinstance(error, ConversationAccessDeniedError):
        return ObservationOutcome.DENIED
    if isinstance(error, ConversationInputError):
        return ObservationOutcome.INVALID
    if isinstance(error, ConversationNotFoundError):
        return ObservationOutcome.NOT_FOUND
    if isinstance(error, ConversationUnavailableError):
        return ObservationOutcome.UNAVAILABLE
    return ObservationOutcome.ERROR


def _uuid(value: object) -> UUID:
    try:
        return value if isinstance(value, UUID) else UUID(str(value))
    except ValueError:
        raise ConversationNotFoundError() from None


def _bounded(value: object, default: int, maximum: int) -> int:
    if value is None:
        return default
    if type(value) is not int or not 1 <= value <= maximum:
        raise ConversationInputError(ConversationInputReason.FILTER_INVALID)
    return value


class ConversationReadService:
    def __init__(
        self,
        repository: ConversationRepository,
        gate: GovernanceGate,
        *,
        connections: IntegrationConnectionRepository | None = None,
        catalog: IntegrationCatalog | None = None,
        observability: ProductObservability | None = None,
    ) -> None:
        self._repository = repository
        self._gate = gate
        self._connections = connections
        self._catalog = catalog
        self._observability = observability

    def _authorize(self, context: RequestContext) -> ActorContext:
        actor = context.actor
        if actor is None:
            raise ConversationAccessDeniedError()
        decision = self._gate.decide(actor, ActionIntent(name=CONVERSATIONS_READ.name),
                                     ActionScope(company_id=actor.company_id))  # fmt: skip
        if decision.outcome is not PolicyOutcome.ALLOW:
            raise ConversationAccessDeniedError()
        return actor

    async def _labels(self, company_id: str) -> dict[UUID, str]:
        if self._connections is None:
            return {}
        try:
            connections = await self._connections.list(company_id)
        except ConnectionRepositoryError:
            return {}  # labels are optional: the transcript itself is still readable
        return {c.connection_id: c.display_name for c in connections}

    def _integration_name(self, integration_id: str) -> str | None:
        installed = self._catalog.get(integration_id) if self._catalog is not None else None
        return installed.definition.name if installed is not None else None

    async def _visible(self, actor: ActorContext, conversation_id: object) -> Conversation:
        wanted = _uuid(conversation_id)
        try:
            found = await self._repository.get_conversation(actor.company_id, wanted)
        except ConversationRepositoryError:
            raise ConversationUnavailableError() from None
        if (
            found is None
            or found.company_id != actor.company_id
            or (found.store_id is not None and found.store_id not in actor.store_ids)
        ):
            raise ConversationNotFoundError()
        return found

    async def _summaries(
        self, actor: ActorContext, conversations: tuple[Conversation, ...]
    ) -> tuple[ConversationSummary, ...]:
        names = await self._labels(actor.company_id)
        return tuple(
            ConversationSummary(
                c,
                ChannelLabel(self._integration_name(c.integration_id), names.get(c.connection_id)),
            )  # fmt: skip
            for c in conversations
        )

    async def list_conversations(
        self, context: RequestContext, *, limit: object = None, connection_id: object = None
    ) -> tuple[ConversationSummary, ...]:
        with observe(self._observability, ProductOperation.CONVERSATION_READ,
                     context.request_id) as obs:  # fmt: skip
            try:
                actor = self._authorize(context)
                size = _bounded(limit, DEFAULT_CONVERSATIONS, MAX_CONVERSATIONS)
                connection = None
                if connection_id is not None:
                    try:
                        connection = _uuid(connection_id)
                    except ConversationNotFoundError:
                        raise ConversationInputError(
                            ConversationInputReason.FILTER_INVALID
                        ) from None
                try:
                    found = await self._repository.list_conversations(
                        actor.company_id, store_ids=actor.store_ids, connection_id=connection,
                        limit=size)  # fmt: skip
                except ConversationRepositoryError:
                    raise ConversationUnavailableError() from None
                result = await self._summaries(actor, found)
            except ConversationError as error:
                obs.finish(_outcome(error), ObservationDetails(
                    business=BusinessDetails(status=ConversationRead.LIST)))  # fmt: skip
                raise
            obs.finish(
                ObservationOutcome.COMPLETED,
                ObservationDetails(business=BusinessDetails(status=ConversationRead.LIST)),
            )
            return result

    async def get_conversation(
        self, context: RequestContext, conversation_id: object
    ) -> ConversationSummary:
        with observe(self._observability, ProductOperation.CONVERSATION_READ,
                     context.request_id) as obs:  # fmt: skip
            try:
                actor = self._authorize(context)
                conversation = await self._visible(actor, conversation_id)
                (summary,) = await self._summaries(actor, (conversation,))
            except ConversationError as error:
                obs.finish(_outcome(error), ObservationDetails(
                    business=BusinessDetails(status=ConversationRead.GET)))  # fmt: skip
                raise
            obs.finish(
                ObservationOutcome.COMPLETED,
                ObservationDetails(business=BusinessDetails(status=ConversationRead.GET)),
            )
            return summary

    async def list_messages(
        self, context: RequestContext, conversation_id: object, *,
        before_sequence: object = None, limit: object = None,
    ) -> MessagePage:  # fmt: skip
        with observe(self._observability, ProductOperation.CONVERSATION_READ,
                     context.request_id) as obs:  # fmt: skip
            try:
                actor = self._authorize(context)
                size = _bounded(limit, DEFAULT_MESSAGES, MAX_MESSAGES)
                if before_sequence is not None and (type(before_sequence) is not int
                                                    or before_sequence < 2):  # fmt: skip
                    raise ConversationInputError(ConversationInputReason.FILTER_INVALID)
                conversation = await self._visible(actor, conversation_id)
                try:
                    messages = await self._repository.list_messages(
                        actor.company_id, conversation.conversation_id,
                        before_sequence=before_sequence, limit=size)  # fmt: skip
                except ConversationRepositoryError:
                    raise ConversationUnavailableError() from None
            except ConversationError as error:
                obs.finish(_outcome(error), ObservationDetails(
                    business=BusinessDetails(status=ConversationRead.MESSAGES)))  # fmt: skip
                raise
            obs.finish(ObservationOutcome.COMPLETED, ObservationDetails(
                business=BusinessDetails(status=ConversationRead.MESSAGES)))  # fmt: skip
            oldest = messages[0].sequence if messages else None
            return MessagePage(messages, oldest if oldest is not None and oldest > 1 else None)
