"""Origin lock, redirects, environment isolation, TLS, cookies, headers and non-leakage."""

import asyncio
import inspect
import logging

import httpx
import pytest

from app.integrations.http import (
    HttpMethod,
    HttpxIntegrationTransport,
    IntegrationHttpRequest,
    IntegrationHttpRequestInvalidError,
    IntegrationHttpTransportError,
    IntegrationHttpUnavailableError,
)
from app.integrations.http import httpx_transport as module
from tests.integration_http.support import (
    ORIGIN,
    SECRET,
    Server,
    Sleeps,
    call,
    req,
    response,
    run,
    transport,
)


def test_every_request_goes_to_exactly_the_configured_origin() -> None:
    server = Server(lambda r: response(200))
    t = transport(server, base_url="https://api.example.com:8443/")

    async def main():
        for path in ("/", "/v1/orders", "/a:b@c/d", "/x%2Fnot-a-slash-escape".replace("%2F", "-")):
            await t.request(req(path=path, query=[("next", "https://evil.example/x")]))
        await t.close()

    run(main())
    for sent in server.requests:
        assert (sent.url.scheme, sent.url.host, sent.url.port) == ("https", "api.example.com", 8443)
        assert sent.url.userinfo == b""
        assert sent.headers["host"] == "api.example.com:8443"


def test_a_tampered_request_is_revalidated_before_use() -> None:
    server = Server(response(200))
    request = req()
    object.__setattr__(request, "path", "//evil.example/steal")  # bypassing the frozen model
    with pytest.raises(IntegrationHttpRequestInvalidError) as caught:
        run(call(transport(server), request))
    assert caught.value.request_may_have_been_sent is False
    assert server.requests == []
    for bad in ("not a request", None):
        with pytest.raises(IntegrationHttpRequestInvalidError):
            run(call(transport(server), bad))  # type: ignore[arg-type]


def test_redirects_are_returned_not_followed_and_credentials_stay_home() -> None:
    server = Server(response(302, headers={"Location": "https://other-origin.example/steal"}),
                    response(200, b"never"))  # fmt: skip
    t = transport(server, default_headers={"Authorization": f"Bearer {SECRET}"})
    result = run(call(t, req()))
    assert result.status_code == 302
    assert result.headers["location"] == "https://other-origin.example/steal"
    (only,) = server.requests  # no second request anywhere
    assert only.url.host == "api.example.com"
    assert only.headers["authorization"] == f"Bearer {SECRET}"


@pytest.mark.parametrize("status", [301, 303, 307, 308])
def test_no_redirect_status_is_followed(status: int) -> None:
    server = Server(response(status, headers={"Location": "/elsewhere"}), response(200))
    assert run(call(transport(server), req())).status_code == status
    assert len(server.requests) == 1


def test_environment_proxies_are_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy"):
        monkeypatch.setenv(name, "http://proxy.invalid:3128")
    monkeypatch.setenv("NETRC", "/nonexistent/netrc")
    server = Server(response(200, b"direct"))
    result = run(call(transport(server), req()))
    assert result.body == b"direct" and server.requests[0].url.host == "api.example.com"
    assert "proxy-authorization" not in server.requests[0].headers


def test_production_client_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    """Captures what the production transport hands to httpx (no network is used)."""
    captured: dict[str, dict] = {}
    real_pool, real_client = httpx.AsyncHTTPTransport, httpx.AsyncClient

    def pool(**kwargs):
        captured["pool"] = kwargs
        return real_pool(**kwargs)

    def client(**kwargs):
        captured["client"] = kwargs
        return real_client(**kwargs)

    monkeypatch.setattr(module.httpx, "AsyncHTTPTransport", pool)
    monkeypatch.setattr(module.httpx, "AsyncClient", client)
    t = HttpxIntegrationTransport(ORIGIN)
    run(t.close())
    pool_kwargs, client_kwargs = captured["pool"], captured["client"]
    assert pool_kwargs["verify"] is True and pool_kwargs["trust_env"] is False
    assert pool_kwargs["retries"] == 0
    limits = pool_kwargs["limits"]
    assert (limits.max_connections, limits.max_keepalive_connections) == (20, 10)
    assert client_kwargs["follow_redirects"] is False
    assert client_kwargs["trust_env"] is False and client_kwargs["verify"] is True
    timeout = client_kwargs["timeout"]
    assert (timeout.connect, timeout.read, timeout.write, timeout.pool) == (5.0, 15.0, 15.0, 5.0)
    assert client_kwargs["transport"] is not None  # never an implicit default pool


def test_source_never_weakens_tls_or_trusts_the_environment() -> None:
    source = inspect.getsource(module)
    assert "verify=False" not in source and "trust_env=True" not in source
    assert "follow_redirects=True" not in source
    # Both the pooled transport and the client set them (plus docstring mentions).
    assert source.count("trust_env=False,") == 2 and source.count("verify=True,") == 2
    assert "follow_redirects=False" in source


def test_no_cookie_state_is_kept_between_requests() -> None:
    server = Server(response(200, headers={"Set-Cookie": "session=abc; Path=/"}))
    t = transport(server)

    async def main():
        await t.request(req())
        await t.request(req())
        await t.close()

    run(main())
    assert all("cookie" not in r.headers for r in server.requests)


def test_trusted_authorization_is_sent_and_request_headers_override_defaults() -> None:
    server = Server(response(200))
    t = transport(server, default_headers={"Authorization": "Bearer default", "X-Api": "k"})

    async def main():
        await t.request(req())
        await t.request(req(headers={"Authorization": "Bearer per-request"}))
        await t.close()

    run(main())
    first, second = server.requests
    assert first.headers["authorization"] == "Bearer default" and first.headers["x-api"] == "k"
    assert second.headers.get_list("authorization") == ["Bearer per-request"]
    assert first.headers["accept-encoding"] == "gzip, deflate"


@pytest.mark.parametrize("headers", [{"Host": "evil.example"}, {"Proxy-Authorization": "x"},
                                     {"X-A": "a\r\nHost: evil.example"}])  # fmt: skip
def test_unsafe_default_headers_are_rejected_at_construction(headers) -> None:
    with pytest.raises(ValueError):
        HttpxIntegrationTransport(ORIGIN, default_headers=headers)


def test_secrets_never_appear_in_repr_errors_or_logs(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    server = Server(httpx.ReadTimeout(f"timeout for {ORIGIN}/v1/{SECRET}"))
    t = transport(server, default_headers={"Authorization": f"Bearer {SECRET}"})
    request = req(HttpMethod.POST, f"/v1/{SECRET.lower()}", query=[("token", SECRET)],
                  headers={"X-Api-Key": SECRET}, body=SECRET.encode())  # fmt: skip
    with pytest.raises(IntegrationHttpUnavailableError) as caught:
        run(call(t, request))
    error = caught.value
    rendered = " ".join([repr(t), repr(request), repr(error), str(error), repr(t.policy),
                         repr(error.args)])  # fmt: skip
    for marker in (SECRET, SECRET.lower(), "api.example.com", "/v1", "Bearer", "token"):
        assert marker not in rendered, marker
    assert error.__cause__ is None and error.__suppress_context__ is True
    assert str(error) == "integration_http_unavailable"
    transport_logs = [r for r in caplog.records if r.name.startswith("app")]
    assert transport_logs == []  # the transport logs nothing at all


def test_transport_source_has_no_logging_or_printing() -> None:
    from pathlib import Path  # noqa: PLC0415

    import app.integrations.http as package  # noqa: PLC0415

    for path in Path(package.__file__).parent.glob("*.py"):
        source = path.read_text()
        assert "print(" not in source, path.name
        if path.name != "dependency_logging.py":  # the only (filter-only) logging use
            assert "import logging" not in source and "getLogger" not in source, path.name


def test_response_contains_only_response_data() -> None:
    server = Server(response(200, b"payload", {"X-Resp": "1"}))
    t = transport(server, default_headers={"Authorization": f"Bearer {SECRET}"})
    result = run(call(t, req()))
    assert result.body == b"payload" and result.headers.get("x-resp") == "1"
    assert SECRET not in repr(result.headers.items_list())


def test_errors_are_the_product_types_only() -> None:
    for exc in (httpx.ConnectError("x"), httpx.ReadError("x"), httpx.LocalProtocolError("x"),
                httpx.ProxyError("x"), httpx.UnsupportedProtocol("x"), ValueError("x"),
                OSError("x")):  # fmt: skip
        with pytest.raises(IntegrationHttpTransportError) as caught:
            run(call(transport(Server(exc), Sleeps()), req(HttpMethod.POST)))
        assert not isinstance(caught.value, httpx.HTTPError)


def test_requests_do_not_share_mutable_state() -> None:
    server = Server(lambda r: response(200, r.url.path.encode()))
    t = transport(server)
    before = {name: getattr(t, name) for name in type(t).__slots__}

    async def main():
        results = await asyncio.gather(*(t.request(req(path=f"/items/{n}")) for n in range(25)))
        return results

    results = run(main())
    assert [r.body for r in results] == [f"/items/{n}".encode() for n in range(25)]
    after = {name: getattr(t, name) for name in type(t).__slots__}
    assert after == before  # no per-request attribute was written
    assert not hasattr(t, "__dict__")
    run(t.close())


def test_invalid_request_objects_never_reach_the_network() -> None:
    server = Server(response(200))
    t = transport(server)
    with pytest.raises(ValueError):
        run(call(t, IntegrationHttpRequest(HttpMethod.GET, "https://evil.example/x")))
    assert server.requests == []
