"""Product Conversation API (Task 037): READ-ONLY.

    GET /api/v1/conversations?limit=&connection_id=                     conversations.read
    GET /api/v1/conversations/conversation?conversation_id=             conversations.read
    GET /api/v1/conversations/messages?conversation_id=&before_sequence=&limit=
                                                                        conversations.read

There is deliberately NO ingest/webhook route (provider webhook authentication is
provider-specific: a future adapter exposes its own reviewed route and calls the Product
ingress) and NO send, reply, close or assign route. Paths are FIXED (ids are query
parameters) so the AgentOS exemption stays a list of exact paths. Product authentication
only; never AgentOS. Another company's conversation, or one in a store the actor cannot
access, is indistinguishable from a missing one (404).

Message text is returned to an authorized reader as DATA (untrusted external content):
it is never interpreted here. Error answers are fixed and never echo submitted values.
"""

from collections.abc import Coroutine
from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field

from app.context import CurrentActor, CurrentRequestContext
from app.conversations.errors import (
    ConversationAccessDeniedError,
    ConversationInputError,
    ConversationNotFoundError,
)
from app.conversations.models import ConversationMessage
from app.conversations.service import (
    MAX_CONVERSATIONS,
    MAX_MESSAGES,
    ConversationReadService,
    ConversationSummary,
)
from app.routes.integrations import SafeValidationRoute

CONVERSATIONS_SERVICE_STATE_KEY = "conversation_service"
CONVERSATIONS_PATH = "/api/v1/conversations"
CONVERSATION_PATH = "/api/v1/conversations/conversation"
MESSAGES_PATH = "/api/v1/conversations/messages"
CONVERSATIONS_PATHS = (CONVERSATIONS_PATH, CONVERSATION_PATH, MESSAGES_PATH)

router = APIRouter(tags=["conversations"], route_class=SafeValidationRoute)
_FROZEN = ConfigDict(frozen=True, extra="forbid")


class ChannelView(BaseModel):
    model_config = _FROZEN
    connection_id: UUID
    integration_id: str
    integration_name: str | None = Field(description="None: not installed in this build.")
    connection_name: str | None = Field(description="None: the connection was removed.")


class ConversationView(BaseModel):
    model_config = _FROZEN
    conversation_id: UUID
    store_id: str | None
    external_conversation_ref: str = Field(description="Opaque external reference.")
    channel: ChannelView
    created_at: datetime
    last_message_at: datetime
    last_message_id: UUID | None

    @classmethod
    def of(cls, summary: ConversationSummary) -> "ConversationView":
        c = summary.conversation
        return cls(
            conversation_id=c.conversation_id, store_id=c.store_id,
            external_conversation_ref=c.external_conversation_ref,
            channel=ChannelView(connection_id=c.connection_id, integration_id=c.integration_id,
                                integration_name=summary.channel.integration_name,
                                connection_name=summary.channel.connection_name),
            created_at=c.created_at, last_message_at=c.last_message_at,
            last_message_id=c.last_message_id,
        )  # fmt: skip


class MessageView(BaseModel):
    model_config = _FROZEN
    message_id: UUID
    sequence: int = Field(description="Product order within the conversation.")
    direction: str
    author_kind: str
    external_sender_ref: str | None
    text: str = Field(description="Untrusted external text when inbound: data only.")
    occurred_at: datetime = Field(description="The source (external) time.")
    recorded_at: datetime = Field(description="When the Product recorded it.")
    delivery_state: str

    @classmethod
    def of(cls, message: ConversationMessage) -> "MessageView":
        return cls(message_id=message.message_id, sequence=message.sequence,
                   direction=message.direction.value, author_kind=message.author_kind.value,
                   external_sender_ref=message.external_sender_ref, text=message.text,
                   occurred_at=message.occurred_at, recorded_at=message.recorded_at,
                   delivery_state=message.delivery_state.value)  # fmt: skip


class ConversationListResponse(BaseModel):
    model_config = _FROZEN
    request_id: UUID
    conversations: list[ConversationView] = Field(description="Newest activity first.")


class ConversationResponse(BaseModel):
    model_config = _FROZEN
    request_id: UUID
    conversation: ConversationView


class MessageListResponse(BaseModel):
    model_config = _FROZEN
    request_id: UUID
    conversation_id: UUID
    messages: list[MessageView] = Field(description="Ascending Product sequence.")
    next_before_sequence: int | None = Field(
        description="Pass as before_sequence for older messages; null when none remain."
    )


def _service(request: Request) -> ConversationReadService:
    service = getattr(request.app.state, CONVERSATIONS_SERVICE_STATE_KEY, None)
    if not isinstance(service, ConversationReadService):
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail="Conversations unavailable")  # fmt: skip
    return service


def _http(error: Exception) -> HTTPException:
    if isinstance(error, ConversationAccessDeniedError):
        return HTTPException(status.HTTP_403_FORBIDDEN, detail="Forbidden")
    if isinstance(error, ConversationNotFoundError):
        return HTTPException(status.HTTP_404_NOT_FOUND, detail="Conversation not found")
    if isinstance(error, ConversationInputError):
        return HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"message": error.message, "code": error.reason.value},
        )
    return HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail="Conversations unavailable")


async def _call[T](call: Coroutine[Any, Any, T]) -> T:
    try:
        return await call
    except Exception as error:  # noqa: BLE001 - mapped to fixed, value-free answers
        raise _http(error) from None


ConversationIdQuery = Annotated[UUID, Query(description="A conversation id of your company.")]
_ERRORS: dict[int | str, dict[str, Any]] = {
    401: {"description": "Missing or invalid Product API key."},
    403: {"description": "The actor lacks conversations.read."},
    422: {"description": "Invalid input (stable `code`; submitted values are never echoed)."},
    503: {"description": "Conversations unavailable."},
}
_ONE: dict[int | str, dict[str, Any]] = {
    **_ERRORS,
    404: {"description": "No such conversation visible to you (indistinguishable)."},
}
_READ_ONLY = ("Read-only: there is no ingest, webhook or send endpoint. Message text is "
              "untrusted external data.\n\nRequires the `conversations.read` Product "
              "permission.")  # fmt: skip


@router.get(CONVERSATIONS_PATH, response_model=ConversationListResponse, responses=_ERRORS,
            summary="List conversations",
            description="This company's conversations (store-less or in your stores), "
                        f"newest activity first. {_READ_ONLY}")  # fmt: skip
async def list_conversations(
    context: CurrentRequestContext, actor: CurrentActor, request: Request,
    limit: Annotated[int, Query(ge=1, le=MAX_CONVERSATIONS)] = 50,
    connection_id: Annotated[UUID | None, Query(description="Only this connection.")] = None,
) -> ConversationListResponse:  # fmt: skip
    found = await _call(_service(request).list_conversations(
        context, limit=limit, connection_id=connection_id))  # fmt: skip
    return ConversationListResponse(
        request_id=context.request_id, conversations=[ConversationView.of(s) for s in found]
    )


@router.get(CONVERSATION_PATH, response_model=ConversationResponse, responses=_ONE,
            summary="Get one conversation", description=_READ_ONLY)  # fmt: skip
async def get_conversation(
    conversation_id: ConversationIdQuery, context: CurrentRequestContext, actor: CurrentActor,
    request: Request,
) -> ConversationResponse:  # fmt: skip
    summary = await _call(_service(request).get_conversation(context, conversation_id))
    return ConversationResponse(
        request_id=context.request_id, conversation=ConversationView.of(summary)
    )


@router.get(MESSAGES_PATH, response_model=MessageListResponse, responses=_ONE,
            summary="List a conversation's messages",
            description="A bounded page by Product sequence (keyset: before_sequence). "
                        f"{_READ_ONLY}")  # fmt: skip
async def list_messages(
    conversation_id: ConversationIdQuery, context: CurrentRequestContext, actor: CurrentActor,
    request: Request,
    before_sequence: Annotated[int | None, Query(ge=2)] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_MESSAGES)] = 50,
) -> MessageListResponse:  # fmt: skip
    page = await _call(_service(request).list_messages(
        context, conversation_id, before_sequence=before_sequence, limit=limit))  # fmt: skip
    return MessageListResponse(request_id=context.request_id, conversation_id=conversation_id,
                               messages=[MessageView.of(m) for m in page.messages],
                               next_before_sequence=page.next_before_sequence)  # fmt: skip
