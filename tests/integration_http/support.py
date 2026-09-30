"""TEST-ONLY offline helpers for the integration HTTP transport (no network, ever)."""

import asyncio
from collections.abc import AsyncIterator, Callable, Iterable
from typing import Any

import httpx

from app.integrations.http import (
    HttpMethod,
    HttpxIntegrationTransport,
    IntegrationHttpPolicy,
    IntegrationHttpRequest,
)

ORIGIN = "https://api.example.com"
SECRET = "SUPER-SECRET-AUTH-MARKER"  # noqa: S105 - a leak marker, not a secret


class ChunkStream(httpx.AsyncByteStream):
    """A streamed body: yields ``chunks`` one by one, optionally failing after
    ``fail_after`` chunks; records how many chunks were actually consumed."""

    def __init__(self, chunks: Iterable[bytes], fail_after: int | None = None,
                 error: Exception | None = None) -> None:  # fmt: skip
        self.chunks = list(chunks)
        self.fail_after = fail_after
        self.error = error or httpx.ReadError("OBS-EXCEPTION stream broke " + SECRET)
        self.consumed = 0
        self.closed = False

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for index, chunk in enumerate(self.chunks):
            if self.fail_after is not None and index == self.fail_after:
                raise self.error
            self.consumed += 1
            yield chunk

    async def aclose(self) -> None:
        self.closed = True


def response(status: int = 200, body: bytes = b"", headers: dict[str, str] | None = None,
             stream: httpx.AsyncByteStream | None = None) -> httpx.Response:  # fmt: skip
    return httpx.Response(status, headers=headers or {},
                          stream=stream if stream is not None else ChunkStream([body]))  # fmt: skip


class Server:
    """An in-process fake server: a queue of replies (responses or exceptions to raise)
    and a record of every request that reached it."""

    def __init__(self, *replies: httpx.Response | Exception | Callable[[httpx.Request], Any]):
        self.replies = list(replies)
        self.requests: list[httpx.Request] = []
        self._used: set[int] = set()
        # Streams are one-shot: remember each response's original stream to replay it.
        self._streams = {id(r): r.stream for r in replies if isinstance(r, httpx.Response)}

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        reply = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        if callable(reply) and not isinstance(reply, httpx.Response):
            reply = reply(request)
        if isinstance(reply, Exception):
            raise reply
        original = self._streams.get(id(reply))
        if id(reply) in self._used and isinstance(original, ChunkStream):
            reply = httpx.Response(reply.status_code, headers=reply.headers,
                                   stream=ChunkStream(original.chunks, original.fail_after,
                                                      original.error))  # fmt: skip
        self._used.add(id(reply))
        return reply

    @property
    def mock(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)


class Sleeps:
    def __init__(self) -> None:
        self.delays: list[float] = []

    async def __call__(self, delay: float) -> None:
        self.delays.append(delay)


def transport(server: Server, sleeps: Sleeps | None = None, **kwargs) -> HttpxIntegrationTransport:
    return HttpxIntegrationTransport(kwargs.pop("base_url", ORIGIN), transport=server.mock,
                                     sleep=sleeps or Sleeps(), **kwargs)  # fmt: skip


def req(method: HttpMethod = HttpMethod.GET, path: str = "/v1/orders", **kwargs):
    return IntegrationHttpRequest(method, path, **kwargs)


def run(coro):
    return asyncio.run(coro)


async def call(t: HttpxIntegrationTransport, request: IntegrationHttpRequest):
    try:
        return await t.request(request)
    finally:
        await t.close()


SMALL = IntegrationHttpPolicy(max_response_bytes=10, max_request_bytes=8)
WRITES = (HttpMethod.POST, HttpMethod.PUT, HttpMethod.PATCH, HttpMethod.DELETE)
READS = (HttpMethod.GET, HttpMethod.HEAD)
