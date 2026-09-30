"""Failure classification (request_may_have_been_sent), translation, cancellation, close."""

import asyncio

import httpx
import pytest

from app.integrations.http import (
    HttpMethod,
    IntegrationHttpClosedError,
    IntegrationHttpRequestInvalidError,
    IntegrationHttpRequestTooLargeError,
    IntegrationHttpResponseTooLargeError,
    IntegrationHttpTransportError,
    IntegrationHttpUnavailableError,
)
from tests.integration_http.support import (
    SECRET,
    SMALL,
    WRITES,
    ChunkStream,
    Server,
    Sleeps,
    call,
    req,
    response,
    run,
    transport,
)


@pytest.mark.parametrize("method", WRITES)
@pytest.mark.parametrize(
    ("failure", "may_have_been_sent"),
    [
        (httpx.ConnectTimeout("x"), False),  # no connection was established
        (httpx.PoolTimeout("x"), False),  # no connection was obtained from the pool
        (httpx.ConnectError("x"), False),
        (httpx.WriteTimeout("x"), True),  # transmission had begun
        (httpx.WriteError("x"), True),
        (httpx.ReadTimeout("x"), True),  # sent, no (complete) answer
        (httpx.ReadError("x"), True),
        (httpx.RemoteProtocolError("x"), True),
        (httpx.LocalProtocolError("x"), True),  # ambiguous: conservative
        (httpx.ProxyError("x"), True),
        (RuntimeError("x"), True),
        (ValueError("x"), True),
    ],
)  # fmt: skip
def test_write_failure_classification(method, failure, may_have_been_sent: bool) -> None:
    server = Server(failure)
    with pytest.raises(IntegrationHttpUnavailableError) as caught:
        run(call(transport(server, Sleeps()), req(method, body=b"{}")))
    assert caught.value.request_may_have_been_sent is may_have_been_sent
    assert len(server.requests) == 1


def test_response_stream_failure_on_a_write_is_uncertain() -> None:
    server = Server(response(200, stream=ChunkStream([b"a", b"b"], fail_after=1)))
    with pytest.raises(IntegrationHttpUnavailableError) as caught:
        run(call(transport(server, Sleeps()), req(HttpMethod.POST, body=b"{}")))
    assert caught.value.request_may_have_been_sent is True
    assert len(server.requests) == 1


def test_local_failures_are_definitely_not_sent() -> None:
    server = Server(response(200))
    t = transport(server, policy=SMALL)
    with pytest.raises(IntegrationHttpRequestTooLargeError) as too_large:
        run(t.request(req(HttpMethod.POST, body=b"x" * 9)))
    bad = req(HttpMethod.POST)
    object.__setattr__(bad, "headers", (("Host", "evil.example"),))
    with pytest.raises(IntegrationHttpRequestInvalidError) as invalid:
        run(t.request(bad))
    run(t.close())
    with pytest.raises(IntegrationHttpClosedError) as closed:
        run(t.request(req(HttpMethod.POST)))
    for caught in (too_large, invalid, closed):
        assert caught.value.request_may_have_been_sent is False
    assert server.requests == []


def test_oversized_write_response_is_uncertain() -> None:
    server = Server(response(200, stream=ChunkStream([b"x" * 50])))
    with pytest.raises(IntegrationHttpResponseTooLargeError) as caught:
        run(call(transport(server, policy=SMALL), req(HttpMethod.PUT, body=b"{}")))
    assert caught.value.request_may_have_been_sent is True


def test_reads_populate_the_flag_consistently() -> None:
    for failure, expected in ((httpx.ConnectError("x"), False), (httpx.ReadTimeout("x"), True)):
        with pytest.raises(IntegrationHttpUnavailableError) as caught:
            run(call(transport(Server(failure), Sleeps()), req()))
        assert caught.value.request_may_have_been_sent is expected


@pytest.mark.parametrize(
    "failure",
    [httpx.ReadTimeout(f"GET https://api.example.com/v1?key={SECRET}"),
     httpx.ConnectError(f"Authorization: Bearer {SECRET}"), ValueError(SECRET)],
)  # fmt: skip
def test_raw_exceptions_never_escape_or_chain(failure) -> None:
    with pytest.raises(IntegrationHttpTransportError) as caught:
        run(call(transport(Server(failure), Sleeps()), req(HttpMethod.POST, body=b"{}")))
    error = caught.value
    assert type(error) is IntegrationHttpUnavailableError
    assert error.__cause__ is None and error.__suppress_context__ is True
    assert error.args == ("integration_http_unavailable",)
    assert SECRET not in repr(error) and SECRET not in str(error)


# ----- cancellation -------------------------------------------------------------------------


def test_cancellation_during_a_request_propagates() -> None:
    started = asyncio.Event()

    async def hang(request: httpx.Request) -> httpx.Response:
        started.set()
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    t = transport(Server(response(200)))
    t._client._transport = httpx.MockTransport(hang)  # type: ignore[attr-defined]

    async def main():
        task = asyncio.create_task(t.request(req(HttpMethod.POST, body=b"{}")))
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await t.close()

    run(main())


def test_cancellation_during_retry_backoff_propagates() -> None:
    entered = asyncio.Event()

    async def blocking_sleep(delay: float) -> None:
        entered.set()
        await asyncio.Event().wait()

    server = Server(response(503), response(200))
    t = transport(server, blocking_sleep)  # type: ignore[arg-type]

    async def main():
        task = asyncio.create_task(t.request(req()))
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await t.close()

    run(main())
    assert len(server.requests) == 1


# ----- lifecycle ----------------------------------------------------------------------------


def test_close_is_async_idempotent_and_final() -> None:
    server = Server(response(200))
    t = transport(server)
    client = t._client  # type: ignore[attr-defined]
    closes = []
    original = client.aclose

    async def counting_close():
        closes.append(1)
        await original()

    client.aclose = counting_close  # type: ignore[method-assign]

    async def main():
        assert (await t.request(req())).status_code == 200
        await t.close()
        await t.close()
        await t.close()
        with pytest.raises(IntegrationHttpClosedError) as caught:
            await t.request(req())
        return caught.value

    error = run(main())
    assert closes == [1]  # closed exactly once
    assert error.request_may_have_been_sent is False and str(error) == "integration_http_closed"
    assert t._client is client and client.is_closed  # type: ignore[attr-defined]  # never recreated
    assert len(server.requests) == 1


def test_close_racing_an_in_flight_request_fails_safely() -> None:
    release = asyncio.Event()

    async def slow(request: httpx.Request) -> httpx.Response:
        await release.wait()
        return response(200, b"late")

    t = transport(Server(response(200)))
    t._client._transport = httpx.MockTransport(slow)  # type: ignore[attr-defined]

    async def main():
        task = asyncio.create_task(t.request(req()))
        await asyncio.sleep(0)
        await t.close()
        release.set()
        try:
            return await task
        except IntegrationHttpTransportError as error:
            return error

    outcome = run(main())
    assert not isinstance(outcome, httpx.HTTPError)
    with pytest.raises(IntegrationHttpClosedError):
        run(t.request(req()))
