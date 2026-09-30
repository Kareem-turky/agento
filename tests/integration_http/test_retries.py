"""Automatic retries: GET/HEAD only, bounded, deterministic; writes exactly once."""

import httpx
import pytest

from app.integrations.http import (
    IntegrationHttpPolicy,
    IntegrationHttpUnavailableError,
)
from tests.integration_http.support import (
    READS,
    WRITES,
    Server,
    Sleeps,
    call,
    req,
    response,
    run,
    transport,
)


@pytest.mark.parametrize("method", READS)
@pytest.mark.parametrize(
    "failure",
    [httpx.ConnectError("x"), httpx.ConnectTimeout("x"), httpx.ReadTimeout("x"),
     httpx.ReadError("x"), httpx.WriteTimeout("x"), httpx.PoolTimeout("x"),
     httpx.RemoteProtocolError("x")],
)  # fmt: skip
def test_reads_retry_transient_failures_then_succeed(method, failure) -> None:
    server, sleeps = Server(failure, response(200, b"ok")), Sleeps()
    result = run(call(transport(server, sleeps), req(method)))
    assert result.status_code == 200 and len(server.requests) == 2
    assert sleeps.delays == [0.25]


@pytest.mark.parametrize("status", [429, 502, 503, 504])
def test_get_retries_transient_statuses(status: int) -> None:
    server, sleeps = Server(response(status), response(200, b"ok")), Sleeps()
    result = run(call(transport(server, sleeps), req()))
    assert (result.status_code, result.body) == (200, b"ok")
    assert len(server.requests) == 2 and sleeps.delays == [0.25]


def test_repeated_503_stops_at_max_attempts_and_returns_the_last_response() -> None:
    server, sleeps = Server(response(503, b"busy")), Sleeps()
    result = run(call(transport(server, sleeps), req()))
    assert (result.status_code, result.body) == (503, b"busy")
    assert len(server.requests) == 3 and sleeps.delays == [0.25, 0.5]  # exponential


def test_exhausted_transient_failures_raise_the_safe_error() -> None:
    server, sleeps = Server(httpx.ConnectError("x")), Sleeps()
    with pytest.raises(IntegrationHttpUnavailableError) as caught:
        run(call(transport(server, sleeps), req()))
    assert len(server.requests) == 3 and sleeps.delays == [0.25, 0.5]
    assert caught.value.request_may_have_been_sent is False


def test_max_attempts_is_policy_bound() -> None:
    policy = IntegrationHttpPolicy(max_read_attempts=5, base_retry_delay_seconds=1.0)
    server, sleeps = Server(response(502)), Sleeps()
    run(call(transport(server, sleeps, policy=policy), req()))
    assert len(server.requests) == 5 and sleeps.delays == [1.0, 2.0, 4.0, 8.0]
    single = Server(response(502))
    run(call(transport(single, Sleeps(), policy=IntegrationHttpPolicy(max_read_attempts=1)),
             req()))  # fmt: skip
    assert len(single.requests) == 1


def test_mid_stream_read_failure_is_retried_for_reads() -> None:
    from tests.integration_http.support import ChunkStream  # noqa: PLC0415

    broken = response(200, stream=ChunkStream([b"par", b"tial"], fail_after=1))
    server, sleeps = Server(broken, response(200, b"whole")), Sleeps()
    assert run(call(transport(server, sleeps), req())).body == b"whole"
    assert len(server.requests) == 2


@pytest.mark.parametrize(
    ("value", "delay"),
    [("2", 2.0), ("0", 0.0), (" 3 ", 3.0), ("999", 5.0), ("99999999", 5.0),
     ("Wed, 21 Oct 2015 07:28:00 GMT", 0.25), ("-1", 0.25), ("1.5", 0.25), ("abc", 0.25),
     ("", 0.25), ("1e9", 0.25)],
)  # fmt: skip
def test_retry_after_is_numeric_and_bounded(value: str, delay: float) -> None:
    server, sleeps = Server(response(429, headers={"Retry-After": value}), response(200)), Sleeps()
    assert run(call(transport(server, sleeps), req())).status_code == 200
    assert sleeps.delays == [delay]


@pytest.mark.parametrize("status", [400, 401, 403, 404, 409, 422, 500, 501])
def test_other_statuses_are_returned_after_one_attempt(status: int) -> None:
    server, sleeps = Server(response(status, b"detail")), Sleeps()
    result = run(call(transport(server, sleeps), req()))
    assert (result.status_code, result.body) == (status, b"detail")
    assert len(server.requests) == 1 and sleeps.delays == []


@pytest.mark.parametrize("method", WRITES)
@pytest.mark.parametrize("status", [429, 502, 503, 504])
def test_writes_never_retry_transient_statuses(method, status: int) -> None:
    server, sleeps = Server(response(status, headers={"Retry-After": "1"}),
                            response(201, b"created")), Sleeps()  # fmt: skip
    request = req(method, body=b"{}", headers={"Idempotency-Key": "abc"})
    result = run(call(transport(server, sleeps), request))
    assert result.status_code == status  # the first response, returned as is
    assert len(server.requests) == 1 and sleeps.delays == []


@pytest.mark.parametrize("method", WRITES)
@pytest.mark.parametrize(
    "failure",
    [httpx.ReadTimeout("x"), httpx.ConnectError("x"), httpx.ConnectTimeout("x"),
     httpx.PoolTimeout("x"), httpx.WriteTimeout("x"), httpx.RemoteProtocolError("x"),
     httpx.ReadError("x")],
)  # fmt: skip
def test_writes_never_retry_transport_failures(method, failure) -> None:
    server, sleeps = Server(failure, response(201)), Sleeps()
    with pytest.raises(IntegrationHttpUnavailableError):
        run(call(transport(server, sleeps), req(method, body=b"{}",
                                                headers={"Idempotency-Key": "k"})))  # fmt: skip
    assert len(server.requests) == 1 and sleeps.delays == []


def test_backoff_uses_the_injected_async_sleep_never_blocking_sleep() -> None:
    import inspect  # noqa: PLC0415

    from app.integrations.http import httpx_transport  # noqa: PLC0415

    source = inspect.getsource(httpx_transport)
    assert "time.sleep" not in source and "import time" not in source
    assert "sleep: Sleep = asyncio.sleep" in source
