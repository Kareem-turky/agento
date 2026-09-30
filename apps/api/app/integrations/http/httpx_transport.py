"""``IntegrationHttpTransport`` over httpx: a secure, bounded outbound client.

Security properties (all enforced here, none configurable per request):

- One trusted origin, validated once: scheme ``https`` (``http`` only with the explicit
  ``allow_insecure_http=True`` code decision), a host, no userinfo, query, fragment or
  application path. Requests carry only a validated relative path, and the built URL is
  re-checked against the origin before anything is sent.
- TLS certificate verification always on; redirects never followed (a 3xx is returned
  as is, so credentials never cross to another origin); environment proxies, netrc and
  other ambient configuration ignored (``trust_env=False``); cookies never stored.
- Explicit connect/read/write/pool timeouts and connection limits from
  ``IntegrationHttpPolicy``.
- Request bodies above the cap fail before any network use. Responses are streamed and
  decoded here with a bounded decoder (identity, gzip, deflate); the DECODED size is
  capped while streaming, and a Content-Length already over the cap aborts early.
- Only GET/HEAD are retried (transient network failures and 429/502/503/504), with
  deterministic exponential backoff or a bounded numeric Retry-After, up to
  ``max_read_attempts``. POST/PUT/PATCH/DELETE are sent exactly once.
- Every failure is a fixed ``IntegrationHttpTransportError`` raised ``from None``, with
  a conservative ``request_may_have_been_sent``. Cancellation always propagates.
- Nothing is logged, and ``repr`` never shows the origin, headers or credentials.
"""

import asyncio
import re
import zlib
from collections.abc import Awaitable, Callable, Iterable, Mapping
from http.cookiejar import CookieJar, DefaultCookiePolicy
from urllib.parse import urlsplit

import httpx

from app.integrations.http.contracts import (
    HttpMethod,
    IntegrationHttpRequest,
    IntegrationHttpResponse,
    ResponseHeaders,
    validate_header,
    validate_path,
    validate_request_header,
)
from app.integrations.http.errors import (
    IntegrationHttpClosedError,
    IntegrationHttpRequestInvalidError,
    IntegrationHttpRequestTooLargeError,
    IntegrationHttpResponseTooLargeError,
    IntegrationHttpTransportError,
    IntegrationHttpUnavailableError,
)
from app.integrations.http.policy import IntegrationHttpPolicy

RETRYABLE_READ_STATUSES = frozenset({429, 502, 503, 504})
DEFAULT_USER_AGENT = "commerce-ai-platform-integration/1"
# The transport decodes responses itself (bounded), so it advertises what it decodes.
ACCEPT_ENCODING = "gzip, deflate"
_RETRY_AFTER_SECONDS = re.compile(r"[0-9]{1,10}")
# Compressed framing (headers, trailers, block overhead) may exceed a small decoded cap:
# raw compressed bytes are allowed this much slack; the DECODED size stays exact.
_COMPRESSION_OVERHEAD = 64 * 1024

Sleep = Callable[[float], Awaitable[None]]


def client_timeout(policy: IntegrationHttpPolicy) -> httpx.Timeout:
    """Explicit connect/read/write/pool timeouts (no client-library defaults)."""
    return httpx.Timeout(
        connect=policy.connect_timeout_seconds,
        read=policy.read_timeout_seconds,
        write=policy.write_timeout_seconds,
        pool=policy.pool_timeout_seconds,
    )


def client_limits(policy: IntegrationHttpPolicy) -> httpx.Limits:
    """A bounded connection pool."""
    return httpx.Limits(
        max_connections=policy.max_connections,
        max_keepalive_connections=policy.max_keepalive_connections,
    )


def validate_base_url(base_url: object, *, allow_insecure_http: bool) -> httpx.URL:
    """The trusted origin: ``scheme://host[:port]`` with an optional trailing "/"."""
    if not isinstance(base_url, str) or any(c.isspace() or ord(c) < 0x20 or c == "\x7f"
                                            for c in base_url):  # fmt: skip
        raise ValueError("invalid integration base URL")
    if "\\" in base_url or "?" in base_url or "#" in base_url:
        raise ValueError("invalid integration base URL")
    parts = urlsplit(base_url)
    scheme = parts.scheme.lower()
    allowed = {"https", "http"} if allow_insecure_http else {"https"}
    if scheme not in allowed:
        raise ValueError("integration base URL must use https")
    if not parts.hostname or parts.username is not None or parts.password is not None:
        raise ValueError("invalid integration base URL")
    if "@" in parts.netloc or parts.path not in ("", "/"):
        raise ValueError("invalid integration base URL")
    try:
        port = parts.port
    except ValueError:
        raise ValueError("invalid integration base URL") from None
    origin = httpx.URL(scheme=scheme, host=parts.hostname, port=port, path="/")
    return origin


def _no_cookies() -> CookieJar:
    """A jar that accepts no cookie: the transport keeps no implicit session state."""
    return CookieJar(policy=DefaultCookiePolicy(allowed_domains=[]))


# ----- bounded response decoding -----------------------------------------------------------


class _BoundedDecoder:
    """Decodes identity/gzip/deflate incrementally without ever producing more than the
    remaining allowance (zlib ``max_length``), so a compression bomb cannot expand."""

    def __init__(self, encoding: str, limit: int) -> None:
        self._limit = limit
        self._total = 0
        self._chunks: list[bytes] = []
        self._zlib: zlib._Decompress | None = None
        self._deflate_probe = False
        self._fed = False
        if encoding == "identity":
            return
        if encoding in ("gzip", "x-gzip"):
            self._zlib = zlib.decompressobj(zlib.MAX_WBITS | 16)
        elif encoding == "deflate":
            self._zlib = zlib.decompressobj(zlib.MAX_WBITS)
            self._deflate_probe = True
        else:
            raise _UnusableResponse

    def _accept(self, data: bytes) -> None:
        self._total += len(data)
        if self._total > self._limit:
            raise IntegrationHttpResponseTooLargeError
        if data:
            self._chunks.append(data)

    def feed(self, raw: bytes) -> None:
        self._fed = self._fed or bool(raw)
        if self._zlib is None:
            self._accept(raw)
            return
        buffer = raw
        while buffer:
            try:
                out = self._zlib.decompress(buffer, self._limit - self._total + 1)
            except zlib.error:
                if not self._deflate_probe:
                    raise _UnusableResponse from None
                # "deflate" is sometimes sent without the zlib wrapper: retry raw.
                self._deflate_probe = False
                self._zlib = zlib.decompressobj(-zlib.MAX_WBITS)
                continue
            self._deflate_probe = False
            self._accept(out)
            buffer = self._zlib.unconsumed_tail
            if self._zlib.eof and (self._zlib.unused_data or buffer):
                raise _UnusableResponse  # trailing data after the compressed stream

    def finish(self) -> bytes:
        if self._zlib is not None:
            out = self._zlib.flush(self._limit - self._total + 1)
            self._accept(out)
            if self._fed and (not self._zlib.eof or self._zlib.unused_data):
                raise _UnusableResponse  # truncated or trailing compressed data
        return b"".join(self._chunks)


class _UnusableResponse(Exception):
    """Internal: a response that cannot be decoded safely."""


class _Retryable(Exception):
    """Internal: a transient failure of a read that may be retried."""

    def __init__(self, error: IntegrationHttpTransportError) -> None:
        super().__init__()
        self.error = error


# ----- exception classification --------------------------------------------------------------


def _not_sent(exc: BaseException) -> bool:
    """Only failures that provably happen before the request leaves this process:
    no connection from the pool, or no connection established."""
    return isinstance(exc, httpx.PoolTimeout | httpx.ConnectTimeout | httpx.ConnectError)


def _transient(exc: BaseException) -> bool:
    return isinstance(exc, httpx.TimeoutException | httpx.NetworkError | httpx.RemoteProtocolError)


class HttpxIntegrationTransport:
    """The Product's secure outbound HTTP transport for one trusted origin.

    ``transport`` is an optional httpx transport for OFFLINE tests only (for example
    ``httpx.MockTransport``); production uses a verified-TLS pooled transport.
    ``default_headers`` are trusted adapter headers (e.g. ``Authorization``) sent with
    every request; they are never logged or exposed.
    """

    __slots__ = ("_client", "_closed", "_default_headers", "_origin", "_policy", "_sleep")

    def __init__(
        self,
        base_url: str,
        *,
        policy: IntegrationHttpPolicy | None = None,
        default_headers: Mapping[str, str] | Iterable[tuple[str, str]] = (),
        allow_insecure_http: bool = False,
        sleep: Sleep = asyncio.sleep,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if allow_insecure_http is not True and allow_insecure_http is not False:
            raise ValueError("allow_insecure_http must be a bool")
        self._policy = policy if policy is not None else IntegrationHttpPolicy()
        if not isinstance(self._policy, IntegrationHttpPolicy):
            raise ValueError("policy must be an IntegrationHttpPolicy")
        self._origin = validate_base_url(base_url, allow_insecure_http=allow_insecure_http)
        items = default_headers.items() if isinstance(default_headers, Mapping) else default_headers
        self._default_headers = tuple(validate_request_header(n, v) for n, v in items)
        self._sleep = sleep
        self._closed = False
        pool = transport if transport is not None else httpx.AsyncHTTPTransport(
            verify=True,
            limits=client_limits(self._policy),
            trust_env=False,
            retries=0,
        )  # fmt: skip
        self._client = httpx.AsyncClient(
            transport=pool,
            timeout=client_timeout(self._policy),
            limits=client_limits(self._policy),
            follow_redirects=False,
            trust_env=False,
            verify=True,
            cookies=_no_cookies(),
            headers={"User-Agent": DEFAULT_USER_AGENT, "Accept-Encoding": ACCEPT_ENCODING},
        )

    def __repr__(self) -> str:
        return f"{type(self).__name__}()"

    @property
    def policy(self) -> IntegrationHttpPolicy:
        return self._policy

    # ----- lifecycle ----------------------------------------------------------------------

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        await self._client.aclose()

    # ----- requests -----------------------------------------------------------------------

    async def request(self, request: IntegrationHttpRequest) -> IntegrationHttpResponse:
        prepared = self._prepare(request)
        attempts = self._policy.max_read_attempts if request.method.is_read else 1
        attempt = 0
        while True:
            attempt += 1
            last = attempt >= attempts
            try:
                response = await self._send_once(prepared, request)
            except _Retryable as retryable:
                if last:
                    raise retryable.error from None
                await self._sleep(self._policy.retry_delay(attempt))
                continue
            if request.method.is_read and response.status_code in RETRYABLE_READ_STATUSES:
                if not last:
                    await self._sleep(self._retry_delay(response, attempt))
                    continue
            return response

    def _retry_delay(self, response: IntegrationHttpResponse, attempt: int) -> float:
        value = response.headers.get("retry-after")
        if value is not None and _RETRY_AFTER_SECONDS.fullmatch(value.strip()):
            return min(float(int(value.strip())), self._policy.max_retry_after_seconds)
        return self._policy.retry_delay(attempt)  # absent, HTTP-date or malformed

    def _prepare(self, request: IntegrationHttpRequest) -> httpx.Request:
        """Validate locally and build the httpx request. Nothing is sent here."""
        if not isinstance(request, IntegrationHttpRequest):
            raise IntegrationHttpRequestInvalidError from None
        if request.body is not None and len(request.body) > self._policy.max_request_bytes:
            raise IntegrationHttpRequestTooLargeError from None
        if self._closed:
            raise IntegrationHttpClosedError from None
        try:
            # Re-validate (the frozen model could have been bypassed) before use.
            validate_path(request.path)
            request_headers = [validate_request_header(n, v) for n, v in request.headers]
            names = {name.lower() for name, _ in request_headers}
            headers = [(n, v) for n, v in self._default_headers if n.lower() not in names]
            headers += request_headers
            for name, value in headers:
                validate_header(name, value)
            url = self._origin.copy_with(path=request.path)
            built = self._client.build_request(
                request.method.value,
                url,
                params=list(request.query) or None,
                headers=headers,
                content=request.body,
            )
        except IntegrationHttpTransportError:
            raise
        except Exception:  # noqa: BLE001 - never leak details of what was invalid
            raise IntegrationHttpRequestInvalidError from None
        # Origin lock: whatever the path, the destination is exactly the trusted origin.
        if (built.url.scheme, built.url.host, built.url.port) != (
            self._origin.scheme, self._origin.host, self._origin.port,
        ) or built.url.userinfo:  # fmt: skip
            raise IntegrationHttpRequestInvalidError from None
        return built

    async def _send_once(
        self, prepared: httpx.Request, request: IntegrationHttpRequest
    ) -> IntegrationHttpResponse:
        is_read = request.method.is_read
        if self._closed:
            raise IntegrationHttpClosedError from None
        response: httpx.Response | None = None
        try:
            try:
                response = await self._client.send(prepared, stream=True)
            except httpx.HTTPError as exc:
                raise self._failure(exc, is_read) from None
            except RuntimeError:
                if self._closed:  # closed concurrently: the client refused to send
                    raise IntegrationHttpClosedError from None
                raise IntegrationHttpUnavailableError(request_may_have_been_sent=True) from None
            except Exception:  # noqa: BLE001 - never leak raw transport errors
                raise IntegrationHttpUnavailableError(request_may_have_been_sent=True) from None
            return await self._read(response, request)
        finally:
            if response is not None:
                try:
                    await response.aclose()
                except Exception:  # noqa: BLE001, S110 - closing never masks the outcome
                    pass

    def _failure(self, exc: BaseException, is_read: bool) -> BaseException:
        error = IntegrationHttpUnavailableError(request_may_have_been_sent=not _not_sent(exc))
        if is_read and _transient(exc):
            return _Retryable(error)
        return error

    async def _read(
        self, response: httpx.Response, request: IntegrationHttpRequest
    ) -> IntegrationHttpResponse:
        limit = self._policy.max_response_bytes
        headers = ResponseHeaders(response.headers.multi_items())
        encodings = [e.strip().lower() for e in headers.get_all("content-encoding")
                     for e in e.split(",") if e.strip()]  # fmt: skip
        encoding = "identity"
        if len(encodings) > 1:
            raise IntegrationHttpUnavailableError(request_may_have_been_sent=True) from None
        if encodings:
            encoding = encodings[0]
        is_head = request.method is HttpMethod.HEAD
        declared = headers.get("content-length")
        if (not is_head and encoding == "identity" and declared is not None
                and declared.strip().isdigit()):  # fmt: skip
            if int(declared.strip()) > limit:  # proven over the cap: do not consume it
                raise IntegrationHttpResponseTooLargeError from None
        try:
            decoder = _BoundedDecoder(encoding, limit)
            raw_total = 0
            raw_limit = limit if encoding == "identity" else limit + _COMPRESSION_OVERHEAD
            if not is_head:  # a HEAD response has no body, whatever it declares
                # Chunks as received (no re-buffering), so the cap stops the read early.
                async for chunk in response.aiter_raw():
                    raw_total += len(chunk)
                    if raw_total > raw_limit:
                        raise IntegrationHttpResponseTooLargeError
                    decoder.feed(chunk)
            body = decoder.finish()
        except IntegrationHttpTransportError:
            raise
        except _UnusableResponse:
            raise IntegrationHttpUnavailableError(request_may_have_been_sent=True) from None
        except httpx.HTTPError as exc:
            # The request was sent; the response broke while streaming.
            error = IntegrationHttpUnavailableError(request_may_have_been_sent=True)
            if request.method.is_read and _transient(exc):
                raise _Retryable(error) from None
            raise error from None
        except Exception:  # noqa: BLE001 - never leak raw stream errors
            raise IntegrationHttpUnavailableError(request_may_have_been_sent=True) from None
        return IntegrationHttpResponse(status_code=response.status_code, headers=headers,
                                       body=body)  # fmt: skip
