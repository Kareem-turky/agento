"""The trusted actor resolution boundary.

A resolver turns an *authenticated* request into an ``ActorContext``. Real
authentication does not exist yet, so the production default resolves no actor
(fail closed). Resolvers must never read identity from client-controlled input
such as ``X-Actor-Id``-style headers, request bodies or prompt text.
"""

from typing import Protocol

from starlette.requests import Request

from app.context.models import ActorContext


class ActorResolver(Protocol):
    async def resolve(self, request: Request) -> ActorContext | None: ...


class NoActorResolver:
    """Production default until authentication exists: never resolves an actor."""

    async def resolve(self, request: Request) -> ActorContext | None:
        return None
