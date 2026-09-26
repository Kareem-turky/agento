"""Creates the ``RequestContext`` for every HTTP request.

    request -> new server request_id -> ActorResolver.resolve(request)
            -> RequestContext -> request.state.request_context -> app

The server-generated request ID is returned in ``X-Request-ID``; any incoming
``X-Request-ID`` is ignored as an internal identifier and replaced in the response.
Implemented as plain ASGI middleware so streaming responses are not buffered.
"""

from starlette.datastructures import MutableHeaders
from starlette.requests import Request
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.context.models import RequestContext
from app.context.resolver import ActorResolver

REQUEST_ID_HEADER = "X-Request-ID"
REQUEST_CONTEXT_STATE_KEY = "request_context"


class RequestContextMiddleware:
    def __init__(self, app: ASGIApp, resolver: ActorResolver) -> None:
        self.app = app
        self.resolver = resolver

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request = Request(scope)
        actor = await self.resolver.resolve(request)
        context = RequestContext(actor=actor, channel="api")
        setattr(request.state, REQUEST_CONTEXT_STATE_KEY, context)

        async def send_with_request_id(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers[REQUEST_ID_HEADER] = str(context.request_id)
            await send(message)

        await self.app(scope, receive, send_with_request_id)
