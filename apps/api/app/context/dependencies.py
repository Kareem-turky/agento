"""FastAPI accessors for the request context.

``require_actor_context`` only checks that a trusted actor is present (401 if not).
It makes no permission decisions; authorization belongs to a later layer.
"""

from typing import Annotated

from fastapi import Depends, HTTPException, Request, status

from app.context.middleware import REQUEST_CONTEXT_STATE_KEY
from app.context.models import ActorContext, RequestContext


def get_request_context(request: Request) -> RequestContext:
    context = getattr(request.state, REQUEST_CONTEXT_STATE_KEY, None)
    if not isinstance(context, RequestContext):
        raise RuntimeError("RequestContextMiddleware is not installed on this application.")
    return context


def require_actor_context(
    context: Annotated[RequestContext, Depends(get_request_context)],
) -> ActorContext:
    if context.actor is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    return context.actor


# Annotated aliases for route signatures, e.g. ``async def route(actor: CurrentActor)``.
CurrentRequestContext = Annotated[RequestContext, Depends(get_request_context)]
CurrentActor = Annotated[ActorContext, Depends(require_actor_context)]
