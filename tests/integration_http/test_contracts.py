"""Transport contract: methods, request/response models, policy and base-URL validation."""

import dataclasses
import math

import pytest

from app.integrations.http import (
    FORBIDDEN_REQUEST_HEADERS,
    HttpMethod,
    HttpxIntegrationTransport,
    IntegrationHttpPolicy,
    IntegrationHttpRequest,
    IntegrationHttpResponse,
    IntegrationHttpTransport,
    ResponseHeaders,
)
from app.integrations.http.httpx_transport import validate_base_url
from tests.integration_http.support import SECRET

# ----- methods ------------------------------------------------------------------------------


def test_fixed_methods_and_read_classification() -> None:
    assert [m.value for m in HttpMethod] == ["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE"]
    assert {m for m in HttpMethod if m.is_read} == {HttpMethod.GET, HttpMethod.HEAD}
    with pytest.raises(ValueError):
        IntegrationHttpRequest("GET", "/v1")  # type: ignore[arg-type]  # strings refused
    with pytest.raises(ValueError):
        HttpMethod("TRACE")


# ----- request model ------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path", ["/", "/v1/orders", "/api/orders/123", "/a/b-c_d.e~f", "/x%20y", "/a:b@c",
             "/files/report.v2.json", "/items/%41"],
)  # fmt: skip
def test_valid_relative_paths(path: str) -> None:
    assert IntegrationHttpRequest(HttpMethod.GET, path).path == path


@pytest.mark.parametrize(
    "path",
    [
        "https://evil.example/orders", "http://evil.example", "//evil.example/path",
        "relative/path", "../path", "/../path", "/a/../b", "/a/./b", "/%2e%2e/secret",
        "/a/%2E%2e", "/a%2fb", "/a%5cb", "/path?secret=x", "/path#fragment", "/a\\b",
        "\\\\evil.example", "/\\evil.example", "/a\r\nHost: evil", "/a\nb", "/a\x00",
        "/a b", "", "/a//b", "/%zz", "/é", ":///x", "evil.example/x",
    ],
)  # fmt: skip
def test_paths_that_could_escape_the_origin_are_rejected(path: str) -> None:
    with pytest.raises(ValueError):
        IntegrationHttpRequest(HttpMethod.GET, path)


def test_query_and_headers_are_ordered_pairs_with_repeats() -> None:
    request = IntegrationHttpRequest(
        HttpMethod.GET, "/v1", query=(("status", "a"), ("status", "b")),
        headers=(("Authorization", "Bearer x"), ("X-Trace", "1")),
    )  # fmt: skip
    assert request.query == (("status", "a"), ("status", "b"))
    assert request.headers == (("Authorization", "Bearer x"), ("X-Trace", "1"))
    # Lists and mappings are also accepted at runtime and normalized to tuples.
    lenient = IntegrationHttpRequest(
        HttpMethod.GET,
        "/v1",
        query=[("a", "1")],  # type: ignore[arg-type]
        headers={"X": "1"},  # type: ignore[arg-type]
    )
    assert (lenient.query, lenient.headers) == ((("a", "1"),), (("X", "1"),))
    with pytest.raises(dataclasses.FrozenInstanceError):
        request.path = "/other"  # type: ignore[misc]


@pytest.mark.parametrize(
    "headers",
    [
        {"X-A": "v\r\nInjected: 1"}, {"X-A": "v\nx"}, {"X-A": "v\x00"}, {"Bad Name": "v"},
        {"X-A\r\n": "v"}, {"": "v"}, {"X-A": 1}, {"Host": "evil.example"},
        {"host": "evil.example"}, {"Proxy-Authorization": "Basic x"}, {"Connection": "close"},
        {"Transfer-Encoding": "chunked"}, {"Content-Length": "1"}, {"Upgrade": "h2c"},
        {"Accept-Encoding": "br"}, {"Keep-Alive": "1"}, {"TE": "trailers"},
    ],
)  # fmt: skip
def test_unsafe_or_transport_owned_headers_are_rejected(headers) -> None:
    with pytest.raises(ValueError):
        IntegrationHttpRequest(HttpMethod.GET, "/v1", headers=headers)


def test_forbidden_header_set_covers_destination_and_hop_by_hop() -> None:
    assert {"host", "connection", "proxy-authorization", "transfer-encoding",
            "upgrade", "te", "keep-alive"} <= FORBIDDEN_REQUEST_HEADERS  # fmt: skip
    assert "authorization" not in FORBIDDEN_REQUEST_HEADERS  # adapters authenticate


@pytest.mark.parametrize("body", ["text", bytearray(b"x"), memoryview(b"x"), 1])
def test_body_must_be_bytes_or_none(body) -> None:
    with pytest.raises(ValueError):
        IntegrationHttpRequest(HttpMethod.POST, "/v1", body=body)


@pytest.mark.parametrize("query", [[("a", 1)], [("a",)], "a=b", [(1, "b")]])
def test_query_must_be_string_pairs(query) -> None:
    with pytest.raises(ValueError):
        IntegrationHttpRequest(HttpMethod.GET, "/v1", query=query)


def test_request_repr_shows_only_the_method() -> None:
    request = IntegrationHttpRequest(
        HttpMethod.POST, "/v1/customers/42", query=(("q", "x"),),
        headers=(("Authorization", f"Bearer {SECRET}"),), body=b"secret body",
    )  # fmt: skip
    assert repr(request) == "IntegrationHttpRequest(method=POST)"


# ----- response model -----------------------------------------------------------------------


def test_response_model_is_immutable_and_bounded() -> None:
    headers = ResponseHeaders([("Set-Cookie", "a=1"), ("set-cookie", "b=2"), ("X", "y")])
    response = IntegrationHttpResponse(status_code=200, headers=headers, body=b"ok")
    assert headers.get_all("SET-COOKIE") == ("a=1", "b=2") and headers["x"] == "y"
    assert len(headers) == 2 and "set-cookie" in headers
    with pytest.raises(dataclasses.FrozenInstanceError):
        response.body = b"changed"  # type: ignore[misc]
    with pytest.raises(TypeError):
        headers["x"] = "z"  # type: ignore[index]
    assert repr(response) == "IntegrationHttpResponse(status_code=200)"
    for bad in ({"status_code": 99}, {"status_code": 600}, {"status_code": True},
                {"body": "text"}, {"headers": {"a": "b"}}):  # fmt: skip
        data = {"status_code": 200, "headers": headers, "body": b""} | bad
        with pytest.raises(ValueError):
            IntegrationHttpResponse(**data)


def test_transport_implements_the_product_protocol() -> None:
    transport = HttpxIntegrationTransport("https://api.example.com")
    assert isinstance(transport, IntegrationHttpTransport)


# ----- policy -------------------------------------------------------------------------------


def test_policy_defaults() -> None:
    policy = IntegrationHttpPolicy()
    assert dataclasses.asdict(policy) == {
        "connect_timeout_seconds": 5.0, "read_timeout_seconds": 15.0,
        "write_timeout_seconds": 15.0, "pool_timeout_seconds": 5.0, "max_read_attempts": 3,
        "base_retry_delay_seconds": 0.25, "max_retry_after_seconds": 5.0,
        "max_request_bytes": 2 * 1024 * 1024, "max_response_bytes": 8 * 1024 * 1024,
        "max_connections": 20, "max_keepalive_connections": 10,
    }  # fmt: skip
    assert [policy.retry_delay(n) for n in (1, 2, 3)] == [0.25, 0.5, 1.0]
    with pytest.raises(dataclasses.FrozenInstanceError):
        policy.max_read_attempts = 9  # type: ignore[misc]


@pytest.mark.parametrize(
    "overrides",
    [
        {"connect_timeout_seconds": 0}, {"read_timeout_seconds": -1},
        {"write_timeout_seconds": math.inf}, {"pool_timeout_seconds": math.nan},
        {"read_timeout_seconds": True}, {"max_read_attempts": 0},
        {"max_read_attempts": 1.5}, {"max_read_attempts": True},
        {"base_retry_delay_seconds": -0.1}, {"max_retry_after_seconds": -1},
        {"max_request_bytes": -1}, {"max_response_bytes": -1}, {"max_connections": 0},
        {"max_keepalive_connections": -1}, {"max_connections": 5, "max_keepalive_connections": 6},
        {"connect_timeout_seconds": "5"},
    ],
)  # fmt: skip
def test_invalid_policy_values_are_rejected(overrides) -> None:
    with pytest.raises(ValueError):
        IntegrationHttpPolicy(**overrides)


# ----- base origin ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("base", "origin"),
    [("https://api.example.com", "https://api.example.com/"),
     ("https://api.example.com/", "https://api.example.com/"),
     ("https://API.example.com:8443", "https://api.example.com:8443/"),
     ("HTTPS://api.example.com", "https://api.example.com/")],
)  # fmt: skip
def test_valid_base_urls(base: str, origin: str) -> None:
    assert str(validate_base_url(base, allow_insecure_http=False)) == origin


@pytest.mark.parametrize(
    "base",
    [
        "http://api.example.com", "ftp://api.example.com", "api.example.com",
        "https://", "https://user@api.example.com", "https://user:pw@api.example.com",
        "https://:pw@api.example.com", "https://api.example.com/v1", "https://api.example.com/v1/",
        "https://api.example.com?x=1", "https://api.example.com/?", "https://api.example.com#f",
        "https://api.example.com:99999", "https://api.example.com:abc", "https://api .example.com",
        "https://api.example.com\\@evil.example", " https://api.example.com",
        "https://api.example.com\n", "https://evil.example@api.example.com", "", None, 1,
    ],
)  # fmt: skip
def test_invalid_base_urls_are_rejected(base) -> None:
    with pytest.raises(ValueError):
        HttpxIntegrationTransport(base)


def test_plain_http_requires_the_explicit_trusted_opt_in() -> None:
    with pytest.raises(ValueError):
        HttpxIntegrationTransport("http://internal.example")
    assert str(validate_base_url("http://internal.example", allow_insecure_http=True)) == (
        "http://internal.example/"
    )
    HttpxIntegrationTransport("http://internal.example", allow_insecure_http=True)
    with pytest.raises(ValueError):
        HttpxIntegrationTransport("http://internal.example", allow_insecure_http=1)  # type: ignore[arg-type]
