"""Trusted actor and request context (request-scoped, never global)."""

from app.context.dependencies import (
    CurrentActor,
    CurrentRequestContext,
    get_request_context,
    require_actor_context,
)
from app.context.middleware import REQUEST_ID_HEADER, RequestContextMiddleware
from app.context.models import ActorContext, ActorType, Channel, RequestContext
from app.context.resolver import ActorResolver, NoActorResolver

__all__ = [
    "REQUEST_ID_HEADER",
    "ActorContext",
    "ActorResolver",
    "ActorType",
    "CurrentActor",
    "CurrentRequestContext",
    "Channel",
    "NoActorResolver",
    "RequestContext",
    "RequestContextMiddleware",
    "get_request_context",
    "require_actor_context",
]
