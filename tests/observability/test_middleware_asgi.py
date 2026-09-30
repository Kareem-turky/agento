"""ProductObservabilityMiddleware as plain ASGI: streaming-safe, re-raises unchanged."""

import asyncio
from uuid import UUID

import pytest

from app.context.models import RequestContext
from app.observability import ObservationOutcome, ProductObservabilityMiddleware, ProductOperation
from app.observability.middleware import outcome_for_status
from tests.support.observability import FAILURE_POINTS, FailingObservability, RecordingObservability

Out = ObservationOutcome
CONTEXT = RequestContext()


def http_scope(path="/api/v1/operations/tickets", method="POST", **extra):
    scope = {"type": "http", "path": path, "method": method, "query_string": b"secret=1",
             "headers": [(b"authorization", b"Bearer OBS-AUTH")],
             "state": {"request_context": CONTEXT}}  # fmt: skip
    scope.update(extra)
    return scope


class Boom(Exception):
    pass


def streaming_app(chunks: list[bytes], status: int = 200, fail_after: int | None = None):
    async def app(scope, receive, send):
        await send({"type": "http.response.start", "status": status, "headers": []})
        for index, chunk in enumerate(chunks):
            if fail_after is not None and index == fail_after:
                raise Boom("mid-stream")
            more = index < len(chunks) - 1
            await send({"type": "http.response.body", "body": chunk, "more_body": more})

    return app


def drive(middleware, scope):
    sent: list[dict] = []
    received = 0

    async def receive():
        nonlocal received
        received += 1
        return {"type": "http.request", "body": b"OBS-SECRET-body", "more_body": False}

    async def send(message):
        sent.append(message)

    asyncio.run(middleware(scope, receive, send))
    return sent, received


def test_streaming_response_is_forwarded_chunk_by_chunk_unbuffered() -> None:
    recorder = RecordingObservability()
    order: list[str] = []
    chunks = [b"a", b"b", b"c"]

    async def app(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        for index, chunk in enumerate(chunks):
            await send({"type": "http.response.body", "body": chunk,
                        "more_body": index < 2})  # fmt: skip
            order.append(f"sent-{chunk.decode()}")

    sent: list[dict] = []

    async def send(message):
        order.append(f"out-{message.get('body', b'start').decode()}")
        sent.append(message)

    async def receive():  # never consumed by the middleware
        raise AssertionError("the middleware must not read the request body")

    asyncio.run(ProductObservabilityMiddleware(app, recorder)(http_scope(), receive, send))
    # Each chunk reached the client before the application produced the next one.
    assert order == ["out-start", "out-a", "sent-a", "out-b", "sent-b", "out-c", "sent-c"]
    assert [m.get("body") for m in sent[1:]] == chunks
    (record,) = recorder.records
    assert record.operation is ProductOperation.HTTP_REQUEST
    assert record.request_id == CONTEXT.request_id
    assert record.outcome is Out.COMPLETED
    assert record.attributes == {"http.method": "POST", "http.route": "/api/v1/operations/tickets",
                                 "http.status_code": 200}  # fmt: skip


def test_messages_are_passed_through_identically() -> None:
    recorder = RecordingObservability()
    sent, received = drive(ProductObservabilityMiddleware(streaming_app([b"x"]), recorder),
                           http_scope())  # fmt: skip
    assert sent == [
        {"type": "http.response.start", "status": 200, "headers": []},
        {"type": "http.response.body", "body": b"x", "more_body": False},
    ]
    assert received == 0


@pytest.mark.parametrize("fail_after", [0, 1])
def test_unhandled_failure_is_error_and_reraised_unchanged(fail_after: int) -> None:
    recorder = RecordingObservability()
    error = Boom("OBS-EXCEPTION")

    async def app(scope, receive, send):
        if fail_after == 1:
            await send({"type": "http.response.start", "status": 200, "headers": []})
        raise error

    with pytest.raises(Boom) as caught:
        drive(ProductObservabilityMiddleware(app, recorder), http_scope())
    assert caught.value is error
    (record,) = recorder.records
    assert record.outcome is Out.ERROR
    expected_status = {"http.status_code": 200} if fail_after == 1 else {}
    assert record.attributes == {"http.method": "POST", "http.route": "/api/v1/operations/tickets",
                                 **expected_status}  # fmt: skip


def test_failure_mid_stream_is_error() -> None:
    recorder = RecordingObservability()
    with pytest.raises(Boom):
        drive(ProductObservabilityMiddleware(streaming_app([b"a", b"b"], fail_after=1),
                                             recorder), http_scope())  # fmt: skip
    assert recorder.records[0].outcome is Out.ERROR


@pytest.mark.parametrize(
    "scope",
    [
        {"type": "lifespan"},
        {"type": "websocket", "path": "/api/v1/operations/runs"},
        http_scope(path="/agents"),
        http_scope(path="/api/v1/operations/tickets/"),
        http_scope(path="/API/v1/operations/tickets"),
    ],
)
def test_non_product_scopes_pass_through_unobserved(scope) -> None:
    recorder = RecordingObservability()
    seen = []

    async def app(scope, receive, send):
        seen.append(scope["type"])

    async def never(*args):  # pragma: no cover - not called for these scopes
        raise AssertionError

    asyncio.run(ProductObservabilityMiddleware(app, recorder)(scope, never, never))
    assert seen == [scope["type"]] and recorder.records == []


def test_unknown_methods_and_missing_context_are_bounded() -> None:
    recorder = RecordingObservability()
    drive(ProductObservabilityMiddleware(streaming_app([b""]), recorder),
          http_scope(path="/health", method="PROPFIND-OBS-SECRET", state={}))  # fmt: skip
    (record,) = recorder.records
    assert record.request_id is None
    assert record.attributes["http.method"] == "OTHER"


@pytest.mark.parametrize("where", FAILURE_POINTS)
def test_failing_observability_does_not_affect_the_response(where: str) -> None:
    sent, _ = drive(ProductObservabilityMiddleware(streaming_app([b"a", b"b"], status=201),
                                                   FailingObservability(where)),
                    http_scope())  # fmt: skip
    assert [m.get("status") or m.get("body") for m in sent] == [201, b"a", b"b"]
    error = Boom()

    async def app(scope, receive, send):
        raise error

    with pytest.raises(Boom) as caught:
        drive(ProductObservabilityMiddleware(app, FailingObservability(where)), http_scope())
    assert caught.value is error


@pytest.mark.parametrize(
    ("status", "outcome"),
    [(200, Out.COMPLETED), (201, Out.COMPLETED), (202, Out.COMPLETED), (307, Out.COMPLETED),
     (400, Out.INVALID), (401, Out.DENIED), (403, Out.DENIED), (404, Out.NOT_FOUND),
     (405, Out.INVALID), (409, Out.CONFLICT), (422, Out.INVALID), (429, Out.INVALID),
     (500, Out.ERROR), (502, Out.ERROR), (503, Out.UNAVAILABLE)],
)  # fmt: skip
def test_status_to_outcome(status: int, outcome) -> None:
    assert outcome_for_status(status) is outcome


def test_request_id_comes_only_from_the_trusted_context() -> None:
    recorder = RecordingObservability()
    spoofed = http_scope(state={"request_context": object()},
                         headers=[(b"x-request-id", str(UUID(int=1)).encode())])  # fmt: skip
    drive(ProductObservabilityMiddleware(streaming_app([b""]), recorder), spoofed)
    assert recorder.records[0].request_id is None
