"""Review fix: dependency (httpx/httpcore) logging never leaks integration request data,
and close() keeps the Product-owned error boundary."""

import asyncio
import logging

import httpx
import pytest

from app.integrations.http import (
    HttpMethod,
    IntegrationHttpClosedError,
    IntegrationHttpCloseError,
    IntegrationHttpUnavailableError,
)
from app.integrations.http.dependency_logging import (
    DEPENDENCY_LOGGERS,
    SUPPRESSION_FILTER,
    install_dependency_log_suppression,
    suppressed,
    suppression_active,
)
from tests.integration_http.support import Server, Sleeps, call, req, response, run, transport

MARKERS = (
    "TRANSPORT-PATH-SECRET",
    "TRANSPORT-QUERY-SECRET",
    "TRANSPORT-AUTH-SECRET",
    "TRANSPORT-BODY-SECRET",
    "TRANSPORT-RESPONSE-SECRET",
    "TRANSPORT-ERROR-SECRET",
)


def all_text(caplog: pytest.LogCaptureFixture) -> str:
    return "\n".join(f"{r.name} {r.getMessage()} {r.args!r}" for r in caplog.records)


def secret_request():
    return req(HttpMethod.POST, "/v1/TRANSPORT-PATH-SECRET",
               query=(("q", "TRANSPORT-QUERY-SECRET"),), body=b"TRANSPORT-BODY-SECRET")  # fmt: skip


# ----- 1-6: nothing from a transport request reaches any log record -----------------------


@pytest.mark.parametrize("level", [logging.INFO, logging.DEBUG])
def test_dependency_request_logs_are_suppressed_at_any_level(caplog, level) -> None:
    caplog.set_level(level)  # root: every logger, including httpx/httpcore
    for name in DEPENDENCY_LOGGERS:
        caplog.set_level(level, logger=name)
    server = Server(
        response(200, b"TRANSPORT-RESPONSE-SECRET"),
    )
    t = transport(server, default_headers={"Authorization": "Bearer TRANSPORT-AUTH-SECRET"})
    run(call(t, secret_request()))
    failing = transport(Server(httpx.ReadTimeout("TRANSPORT-ERROR-SECRET boom")), Sleeps(),
                        default_headers={"X-Api-Key": "TRANSPORT-AUTH-SECRET"})  # fmt: skip
    with pytest.raises(IntegrationHttpUnavailableError):
        run(call(failing, secret_request()))
    text = all_text(caplog)
    for marker in MARKERS:
        assert marker not in text, marker
    for fragment in ("api.example.com", "https://", "/v1/", "HTTP Request"):
        assert fragment not in text, fragment
    assert not [r for r in caplog.records if r.name.split(".")[0] in ("httpx", "httpcore")]


def test_the_same_httpx_logging_still_works_outside_the_transport(caplog) -> None:
    """Proves the capture above would have seen httpx's record, and that unrelated
    httpx usage keeps its normal logging (nothing is globally disabled)."""
    caplog.set_level(logging.INFO)
    install_dependency_log_suppression()

    async def plain_httpx_call():
        async with httpx.AsyncClient(transport=Server(response(200)).mock) as client:
            await client.get("https://unrelated.example/OUTSIDE-MARKER")

    run(plain_httpx_call())
    assert "OUTSIDE-MARKER" in all_text(caplog)
    assert any(r.name == "httpx" for r in caplog.records)


# ----- 7-8: httpcore DEBUG inside vs outside the transport context ------------------------


@pytest.mark.parametrize("name", DEPENDENCY_LOGGERS)
def test_dependency_records_are_dropped_only_inside_the_transport_context(caplog, name) -> None:
    caplog.set_level(logging.DEBUG)
    caplog.set_level(logging.DEBUG, logger=name)
    install_dependency_log_suppression()
    logger = logging.getLogger(name)
    with suppressed():
        assert suppression_active()
        logger.debug("INSIDE-%s", "MARKER")
        logger.error("INSIDE-ERROR-MARKER")
    assert not suppression_active()
    logger.debug("OUTSIDE-%s", "MARKER")
    text = all_text(caplog)
    assert "INSIDE-MARKER" not in text and "INSIDE-ERROR-MARKER" not in text
    assert "OUTSIDE-MARKER" in text


def test_unrelated_loggers_are_never_suppressed(caplog) -> None:
    caplog.set_level(logging.DEBUG)
    install_dependency_log_suppression()
    with suppressed():
        logging.getLogger("app.some.module").warning("PRODUCT-MARKER")
        logging.getLogger("httpxtra").warning("LOOKALIKE-MARKER")  # not the httpx package
    text = all_text(caplog)
    assert "PRODUCT-MARKER" in text and "LOOKALIKE-MARKER" in text


# ----- 9: concurrent tasks are isolated -----------------------------------------------------


def test_concurrent_tasks_do_not_share_the_suppression(caplog) -> None:
    caplog.set_level(logging.DEBUG)
    for name in ("httpx", "httpcore.connection"):
        caplog.set_level(logging.DEBUG, logger=name)
    install_dependency_log_suppression()
    a_inside, b_logged = asyncio.Event(), asyncio.Event()

    async def task_a() -> None:  # inside the secure transport context
        with suppressed():
            a_inside.set()
            await b_logged.wait()  # B logs while A is still inside its context
            logging.getLogger("httpcore.connection").debug("TASK-A-INTEGRATION-MARKER")
            logging.getLogger("httpx").info("TASK-A-HTTPX-MARKER")

    async def task_b() -> None:  # unrelated code, concurrently
        await a_inside.wait()
        logging.getLogger("httpcore.connection").debug("TASK-B-UNRELATED-MARKER")
        logging.getLogger("httpx").info("TASK-B-HTTPX-MARKER")
        b_logged.set()

    async def main():
        await asyncio.gather(task_a(), task_b())

    run(main())
    text = all_text(caplog)
    assert "TASK-A-INTEGRATION-MARKER" not in text and "TASK-A-HTTPX-MARKER" not in text
    assert "TASK-B-UNRELATED-MARKER" in text and "TASK-B-HTTPX-MARKER" in text


def test_real_transport_request_concurrent_with_plain_httpx(caplog) -> None:
    caplog.set_level(logging.INFO)
    release = asyncio.Event()

    async def slow(request: httpx.Request) -> httpx.Response:
        await release.wait()
        return response(200)

    t = transport(Server(response(200)))
    t._client._transport = httpx.MockTransport(slow)  # type: ignore[attr-defined]

    async def secure():
        await t.request(req(path="/v1/TRANSPORT-PATH-SECRET"))

    async def plain():
        async with httpx.AsyncClient(transport=Server(response(200)).mock) as client:
            await client.get("https://unrelated.example/PLAIN-MARKER")
        release.set()

    async def main():
        await asyncio.gather(secure(), plain())
        await t.close()

    run(main())
    text = all_text(caplog)
    assert "PLAIN-MARKER" in text and "TRANSPORT-PATH-SECRET" not in text


# ----- 10-12: idempotent installation, no duplicates, no logger configuration changes -----


def _logger_state():
    names = ["", *DEPENDENCY_LOGGERS]
    return {n: (logging.getLogger(n).level, logging.getLogger(n).disabled,
                logging.getLogger(n).propagate, tuple(logging.getLogger(n).handlers))
            for n in names}  # fmt: skip


def test_installation_is_idempotent_across_many_transports() -> None:
    install_dependency_log_suppression()
    before = _logger_state()
    root_filters = list(logging.getLogger().filters)
    transports = [transport(Server(response(200))) for _ in range(25)]
    for _ in range(10):
        install_dependency_log_suppression()

    async def main():
        for t in transports[:3]:
            await t.request(req())
        for t in transports:
            await t.close()

    run(main())
    for name in DEPENDENCY_LOGGERS:
        assert logging.getLogger(name).filters.count(SUPPRESSION_FILTER) == 1, name
    assert _logger_state() == before  # no level, disabled, propagate or handler change
    assert logging.getLogger().filters == root_filters  # root logger untouched
    assert SUPPRESSION_FILTER not in logging.getLogger().filters


def test_the_context_flag_is_restored_after_success_failure_and_cancellation() -> None:
    run(call(transport(Server(response(200))), req()))
    assert not suppression_active()
    with pytest.raises(IntegrationHttpUnavailableError):
        run(call(transport(Server(httpx.ConnectError("x")), Sleeps()), req(HttpMethod.POST)))
    assert not suppression_active()

    started = asyncio.Event()

    async def hang(request: httpx.Request) -> httpx.Response:
        started.set()
        await asyncio.Event().wait()
        raise AssertionError

    t = transport(Server(response(200)))
    t._client._transport = httpx.MockTransport(hang)  # type: ignore[attr-defined]

    async def main():
        task = asyncio.create_task(t.request(req(HttpMethod.POST)))
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):  # 19: request cancellation propagates
            await task
        assert not suppression_active()
        await t.close()

    run(main())


# ----- 13-18: close() error boundary --------------------------------------------------------


class ClosingTransport(httpx.AsyncBaseTransport):
    """TEST-ONLY: serves 200s; its cleanup fails, hangs or logs, as configured."""

    def __init__(self, error: BaseException | None = None, hang: bool = False,
                 log: bool = False) -> None:  # fmt: skip
        self.error, self.hang, self.log = error, hang, log
        self.closes = 0
        self.close_started = asyncio.Event()

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        return response(200)

    async def aclose(self) -> None:
        self.closes += 1
        self.close_started.set()
        if self.log:
            logging.getLogger("httpcore.connection").debug("close.failed CLOSE-SECRET-MARKER")
        if self.hang:
            await asyncio.Event().wait()
        if self.error is not None:
            raise self.error


@pytest.mark.parametrize(
    "error",
    [RuntimeError("CLOSE-SECRET-MARKER at https://api.example.com"),
     httpx.CloseError("CLOSE-SECRET-MARKER"), OSError("CLOSE-SECRET-MARKER")],
)  # fmt: skip
def test_failing_close_is_a_fixed_product_error_and_final(error, caplog) -> None:
    caplog.set_level(logging.DEBUG)
    closing = ClosingTransport(error=error, log=True)
    t = transport(Server(response(200)))
    t._client._transport = closing  # type: ignore[attr-defined]

    async def main():
        await t.request(req())
        with pytest.raises(IntegrationHttpCloseError) as caught:
            await t.close()
        second = await t.close()  # idempotent: returns normally, no cleanup retry
        with pytest.raises(IntegrationHttpClosedError) as after:
            await t.request(req())
        return caught.value, second, after.value

    failure, second, after = run(main())
    assert type(failure) is IntegrationHttpCloseError and not isinstance(failure, type(error))
    assert str(failure) == "integration_http_close_failed"
    assert "CLOSE-SECRET-MARKER" not in str(failure) and "CLOSE-SECRET-MARKER" not in repr(failure)
    assert "api.example.com" not in repr(failure)
    assert failure.__cause__ is None and failure.__suppress_context__ is True
    assert failure.request_may_have_been_sent is False  # lifecycle failure, not a write
    assert closing.closes == 1 and second is None
    assert after.request_may_have_been_sent is False
    assert t._client._transport is closing  # type: ignore[attr-defined]  # never recreated
    assert "CLOSE-SECRET-MARKER" not in all_text(caplog)  # close-time dependency logs too


def test_close_cancellation_propagates_and_the_transport_stays_closed() -> None:
    closing = ClosingTransport(hang=True)
    t = transport(Server(response(200)))
    t._client._transport = closing  # type: ignore[attr-defined]

    async def main():
        task = asyncio.create_task(t.close())
        await closing.close_started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not suppression_active()
        await t.close()  # returns normally; cleanup is not retried
        with pytest.raises(IntegrationHttpClosedError):
            await t.request(req())

    run(main())
    assert closing.closes == 1


def test_successful_close_still_returns_normally() -> None:
    closing = ClosingTransport()
    t = transport(Server(response(200)))
    t._client._transport = closing  # type: ignore[attr-defined]

    async def main():
        assert await t.close() is None
        assert await t.close() is None

    run(main())
    assert closing.closes == 1
