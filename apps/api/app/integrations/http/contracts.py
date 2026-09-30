"""The Product-owned integration HTTP transport contract.

    Provider adapter -> IntegrationHttpTransport.request(IntegrationHttpRequest)
                     -> IntegrationHttpResponse | IntegrationHttpTransportError

A request names a METHOD and a PATH relative to the transport's trusted, fixed origin:
it can never choose a scheme, host, port or credentials. Query parameters and headers
are explicit ordered pairs (repeated keys allowed); the body is raw bytes. The response
carries the status, read-only headers and the body bytes; the transport never parses
provider data or interprets HTTP statuses (the adapter owns that mapping).

No client-library type crosses this contract. Agents, workflows and models never
receive a transport: only reviewed provider adapters do.
"""

import re
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, runtime_checkable
from urllib.parse import unquote


class HttpMethod(StrEnum):
    GET = "GET"
    HEAD = "HEAD"
    POST = "POST"
    PUT = "PUT"
    PATCH = "PATCH"
    DELETE = "DELETE"

    @property
    def is_read(self) -> bool:
        """Only GET and HEAD are read-safe (the only automatically retried methods).
        Every other method is treated as potentially mutating."""
        return self in _READ_METHODS


_READ_METHODS = frozenset({HttpMethod.GET, HttpMethod.HEAD})

# RFC 3986 path characters: unreserved, sub-delims, ":", "@", "/" and %XX escapes.
_PATH = re.compile(r"/(?:[A-Za-z0-9\-._~!$&'()*+,;=:@/]|%[0-9A-Fa-f]{2})*")
# RFC 9110 token for header names.
_HEADER_NAME = re.compile(r"[!#$%&'*+\-.^_`|~0-9A-Za-z]+")
_FORBIDDEN_VALUE_CHARS = re.compile(r"[\x00-\x08\x0a-\x1f\x7f]")

# Transport-owned or hop-by-hop headers a request may never set: the destination,
# connection management, framing, proxies and content decoding belong to the transport.
FORBIDDEN_REQUEST_HEADERS = frozenset({
    "host", "connection", "keep-alive", "proxy-authorization", "proxy-authenticate",
    "proxy-connection", "te", "trailer", "transfer-encoding", "upgrade", "content-length",
    "accept-encoding", "expect",
})  # fmt: skip


def validate_path(path: object) -> str:
    """A relative-to-origin path: exactly one leading "/", RFC 3986 characters only, no
    empty, "." or ".." segments (also when percent-encoded), no query or fragment."""
    if not isinstance(path, str) or not _PATH.fullmatch(path) or "//" in path:
        raise ValueError("invalid integration request path")
    for segment in path.split("/")[1:]:
        decoded = unquote(segment)
        if decoded in (".", "..") or "/" in decoded or "\\" in decoded:
            raise ValueError("invalid integration request path")
    return path


def validate_header(name: object, value: object) -> tuple[str, str]:
    if not isinstance(name, str) or not _HEADER_NAME.fullmatch(name):
        raise ValueError("invalid integration request header")
    if not isinstance(value, str) or _FORBIDDEN_VALUE_CHARS.search(value):
        raise ValueError("invalid integration request header")
    return name, value


def validate_request_header(name: object, value: object) -> tuple[str, str]:
    pair = validate_header(name, value)
    if pair[0].lower() in FORBIDDEN_REQUEST_HEADERS:
        raise ValueError("forbidden integration request header")
    return pair


def _pairs(values: object, kind: str) -> tuple[tuple[str, str], ...]:
    if isinstance(values, Mapping):
        items: Iterable[object] = values.items()
    elif isinstance(values, str | bytes) or not isinstance(values, Iterable):
        raise ValueError(f"integration request {kind} must be name/value pairs")
    else:
        items = values
    out: list[tuple[str, str]] = []
    for item in items:
        if not isinstance(item, tuple) or len(item) != 2:
            raise ValueError(f"integration request {kind} must be name/value pairs")
        out.append((item[0], item[1]))
    return tuple(out)


@dataclass(frozen=True, slots=True, repr=False)
class IntegrationHttpRequest:
    """One immutable request, validated at construction.

    ``query`` and ``headers`` accept a mapping or (name, value) pairs and are stored as
    tuples (repeated names preserved). ``repr`` shows the method only: paths, query
    values, headers (credentials) and bodies may be sensitive.
    """

    method: HttpMethod
    path: str
    query: tuple[tuple[str, str], ...] = ()
    headers: tuple[tuple[str, str], ...] = ()
    body: bytes | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.method, HttpMethod):
            raise ValueError("integration request method must be an HttpMethod")
        validate_path(self.path)
        query = _pairs(self.query, "query")
        for key, value in query:
            if not isinstance(key, str) or not isinstance(value, str):
                raise ValueError("integration request query must be string pairs")
        headers = tuple(validate_request_header(n, v) for n, v in _pairs(self.headers,
                                                                          "headers"))  # fmt: skip
        if self.body is not None and type(self.body) is not bytes:
            raise ValueError("integration request body must be bytes or None")
        object.__setattr__(self, "query", query)
        object.__setattr__(self, "headers", headers)

    def __repr__(self) -> str:
        return f"IntegrationHttpRequest(method={self.method.value})"


class ResponseHeaders(Mapping[str, str]):
    """Read-only, case-insensitive response headers (repeated values preserved)."""

    __slots__ = ("_items",)

    def __init__(self, items: Iterable[tuple[str, str]] = ()) -> None:
        self._items: tuple[tuple[str, str], ...] = tuple((str(k), str(v)) for k, v in items)

    def __getitem__(self, name: str) -> str:
        values = self.get_all(name)
        if not values:
            raise KeyError(name)
        return ", ".join(values)

    def get_all(self, name: str) -> tuple[str, ...]:
        wanted = name.lower()
        return tuple(v for k, v in self._items if k.lower() == wanted)

    def items_list(self) -> tuple[tuple[str, str], ...]:
        return self._items

    def __iter__(self) -> Iterator[str]:
        seen: dict[str, None] = {}
        for key, _ in self._items:
            seen.setdefault(key.lower())
        return iter(seen)

    def __len__(self) -> int:
        return len({k.lower() for k, _ in self._items})

    def __repr__(self) -> str:
        return f"ResponseHeaders({len(self)} names)"


@dataclass(frozen=True, slots=True, repr=False)
class IntegrationHttpResponse:
    """The final response. Never contains request headers or credentials."""

    status_code: int
    headers: ResponseHeaders
    body: bytes

    def __post_init__(self) -> None:
        if type(self.status_code) is not int or not 100 <= self.status_code <= 599:
            raise ValueError("invalid HTTP status code")
        if not isinstance(self.headers, ResponseHeaders):
            raise ValueError("headers must be ResponseHeaders")
        if type(self.body) is not bytes:
            raise ValueError("body must be bytes")

    def __repr__(self) -> str:
        return f"IntegrationHttpResponse(status_code={self.status_code})"


@runtime_checkable
class IntegrationHttpTransport(Protocol):
    async def request(self, request: IntegrationHttpRequest) -> IntegrationHttpResponse:
        """Send one request to the trusted origin (reads may be retried; writes never).
        Raises only ``IntegrationHttpTransportError`` subclasses."""
        ...

    async def close(self) -> None:
        """Release the transport; idempotent. Later requests fail safely."""
        ...
