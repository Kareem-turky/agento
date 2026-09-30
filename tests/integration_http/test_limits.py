"""Request and (decoded, streamed) response size limits."""

import gzip
import zlib

import pytest

from app.integrations.http import (
    HttpMethod,
    IntegrationHttpPolicy,
    IntegrationHttpRequestTooLargeError,
    IntegrationHttpResponseTooLargeError,
    IntegrationHttpUnavailableError,
)
from tests.integration_http.support import (
    READS,
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

CAP = SMALL.max_response_bytes  # 10


@pytest.mark.parametrize("method", [*READS, *WRITES])
def test_oversized_request_body_fails_before_any_network_use(method) -> None:
    server, sleeps = Server(response(200)), Sleeps()
    with pytest.raises(IntegrationHttpRequestTooLargeError) as caught:
        run(call(transport(server, sleeps, policy=SMALL), req(method, body=b"x" * 9)))
    assert caught.value.request_may_have_been_sent is False
    assert server.requests == [] and sleeps.delays == []  # nothing sent, no retry
    assert str(caught.value) == "integration_http_request_too_large"


def test_request_body_at_the_cap_is_sent() -> None:
    server = Server(response(200))
    run(call(transport(server, policy=SMALL), req(HttpMethod.POST, body=b"x" * 8)))
    assert server.requests[0].content == b"x" * 8


def test_small_response_is_returned_exactly() -> None:
    server = Server(response(201, b"0123456789", {"X-A": "b"}))
    result = run(call(transport(server, policy=SMALL), req()))
    assert (result.status_code, result.body, result.headers["x-a"]) == (201, b"0123456789", "b")


def test_body_exactly_at_the_cap_is_allowed_and_cap_plus_one_rejected() -> None:
    at = Server(response(200, stream=ChunkStream([b"01234", b"56789"])))
    assert run(call(transport(at, policy=SMALL), req())).body == b"0123456789"
    over = Server(response(200, stream=ChunkStream([b"01234", b"56789", b"X"])))
    with pytest.raises(IntegrationHttpResponseTooLargeError):
        run(call(transport(over, policy=SMALL), req()))


def test_declared_content_length_over_the_cap_aborts_without_consuming() -> None:
    stream = ChunkStream([b"x" * 5] * 1000)
    server = Server(response(200, headers={"Content-Length": "5000"}, stream=stream))
    with pytest.raises(IntegrationHttpResponseTooLargeError) as caught:
        run(call(transport(server, Sleeps(), policy=SMALL), req()))
    assert stream.consumed == 0  # proven too large: not read at all
    assert caught.value.request_may_have_been_sent is True
    assert len(server.requests) == 1  # a too-large response is never retried


def test_missing_content_length_is_capped_while_streaming() -> None:
    stream = ChunkStream([b"x" * 4] * 1000)
    server = Server(response(200, stream=stream))
    with pytest.raises(IntegrationHttpResponseTooLargeError):
        run(call(transport(server, policy=SMALL), req()))
    assert stream.consumed <= 3  # stopped as soon as the cap was exceeded


def test_false_small_content_length_is_not_trusted() -> None:
    server = Server(response(200, headers={"Content-Length": "2"},
                             stream=ChunkStream([b"x" * 6, b"y" * 6])))  # fmt: skip
    with pytest.raises(IntegrationHttpResponseTooLargeError):
        run(call(transport(server, policy=SMALL), req()))


def test_decoded_size_is_capped_for_compressed_bodies() -> None:
    bomb = gzip.compress(b"\0" * 5_000_000)  # tiny on the wire, huge decoded
    assert len(bomb) < 10_000
    policy = IntegrationHttpPolicy(max_response_bytes=64 * 1024)
    server = Server(response(200, headers={"Content-Encoding": "gzip",
                                           "Content-Length": str(len(bomb))},
                             stream=ChunkStream([bomb])))  # fmt: skip
    with pytest.raises(IntegrationHttpResponseTooLargeError):
        run(call(transport(server, policy=policy), req()))


@pytest.mark.parametrize(
    ("encoding", "payload"),
    [("gzip", gzip.compress(b"0123456789")), ("x-gzip", gzip.compress(b"0123456789")),
     ("deflate", zlib.compress(b"0123456789")),
     ("deflate", zlib.compress(b"0123456789")[2:-4]),  # raw deflate without the zlib wrapper
     ("identity", b"0123456789")],
)  # fmt: skip
def test_supported_encodings_decode_up_to_the_exact_cap(encoding: str, payload: bytes) -> None:
    chunks = [payload[i : i + 3] for i in range(0, len(payload), 3)]
    server = Server(response(200, headers={"Content-Encoding": encoding},
                             stream=ChunkStream(chunks)))  # fmt: skip
    assert run(call(transport(server, policy=SMALL), req())).body == b"0123456789"


def test_compressed_body_one_byte_over_the_cap_is_rejected() -> None:
    server = Server(response(200, headers={"Content-Encoding": "gzip"},
                             stream=ChunkStream([gzip.compress(b"0123456789X")])))  # fmt: skip
    with pytest.raises(IntegrationHttpResponseTooLargeError):
        run(call(transport(server, policy=SMALL), req()))


@pytest.mark.parametrize(
    ("encoding", "payload"),
    [("br", b"x"), ("gzip, gzip", gzip.compress(b"x")), ("gzip", b"not gzip"),
     ("gzip", gzip.compress(b"0123456789")[:-6]), ("gzip", gzip.compress(b"a") + b"junk")],
)  # fmt: skip
def test_unusable_encodings_fail_safely(encoding: str, payload: bytes) -> None:
    server = Server(response(200, headers={"Content-Encoding": encoding},
                             stream=ChunkStream([payload])))  # fmt: skip
    with pytest.raises(IntegrationHttpUnavailableError) as caught:
        run(call(transport(server, Sleeps(), policy=SMALL), req()))
    assert caught.value.request_may_have_been_sent is True


def test_head_ignores_the_declared_length_of_the_resource() -> None:
    server = Server(response(200, headers={"Content-Length": "999999"}, stream=ChunkStream([])))
    result = run(call(transport(server, policy=SMALL), req(HttpMethod.HEAD)))
    assert (result.status_code, result.body) == (200, b"")


@pytest.mark.parametrize("method", WRITES)
def test_oversized_response_to_a_write_is_uncertain(method) -> None:
    server = Server(response(200, stream=ChunkStream([b"x" * 11])))
    with pytest.raises(IntegrationHttpResponseTooLargeError) as caught:
        run(call(transport(server, policy=SMALL), req(method, body=b"{}")))
    assert caught.value.request_may_have_been_sent is True
    assert len(server.requests) == 1
