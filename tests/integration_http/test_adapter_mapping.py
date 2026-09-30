"""TEST-ONLY demonstration of how a future provider adapter maps transport failures.

The generic transport never raises business errors. A reviewed adapter translates:

    read failure                                  -> IntegrationUnavailableError
    write failure, request_may_have_been_sent=False -> IntegrationWriteRejectedError
    write failure, request_may_have_been_sent=True  -> IntegrationWriteUncertainError

Nothing here is Product runtime code.
"""

import httpx
import pytest

from app.integrations.commerce.errors import (
    IntegrationUnavailableError,
    IntegrationWriteRejectedError,
    IntegrationWriteUncertainError,
)
from app.integrations.http import (
    HttpMethod,
    IntegrationHttpRequest,
    IntegrationHttpTransport,
    IntegrationHttpTransportError,
)
from tests.integration_http.support import (
    SMALL,
    ChunkStream,
    Server,
    Sleeps,
    response,
    run,
    transport,
)


class DemoAdapter:
    """Shape of a future adapter's error handling (test only)."""

    integration_id = "demo"

    def __init__(self, http: IntegrationHttpTransport) -> None:
        self.http = http

    async def read(self) -> bytes:
        try:
            result = await self.http.request(IntegrationHttpRequest(HttpMethod.GET, "/v1/items"))
        except IntegrationHttpTransportError:
            raise IntegrationUnavailableError(self.integration_id) from None
        return result.body

    async def write(self) -> bytes:
        try:
            result = await self.http.request(
                IntegrationHttpRequest(HttpMethod.POST, "/v1/tickets", body=b"{}")
            )
        except IntegrationHttpTransportError as error:
            if error.request_may_have_been_sent:
                raise IntegrationWriteUncertainError("ticket") from None
            raise IntegrationWriteRejectedError("ticket") from None
        return result.body


def test_read_transport_failure_maps_to_unavailable() -> None:
    adapter = DemoAdapter(transport(Server(httpx.ConnectError("x")), Sleeps()))
    with pytest.raises(IntegrationUnavailableError):
        run(adapter.read())


@pytest.mark.parametrize("failure", [httpx.ConnectError("x"), httpx.ConnectTimeout("x"),
                                     httpx.PoolTimeout("x")])  # fmt: skip
def test_write_not_sent_maps_to_rejected(failure) -> None:
    adapter = DemoAdapter(transport(Server(failure), Sleeps()))
    with pytest.raises(IntegrationWriteRejectedError) as caught:
        run(adapter.write())
    assert caught.value.effect_may_have_occurred is False


def test_write_too_large_locally_maps_to_rejected() -> None:
    http = transport(Server(response(200)), Sleeps(), policy=SMALL)

    async def oversized():
        try:
            await http.request(IntegrationHttpRequest(HttpMethod.POST, "/v1", body=b"x" * 9))
        except IntegrationHttpTransportError as error:
            assert error.request_may_have_been_sent is False
            raise IntegrationWriteRejectedError("ticket") from None

    with pytest.raises(IntegrationWriteRejectedError):
        run(oversized())


@pytest.mark.parametrize(
    "reply",
    [httpx.ReadTimeout("x"), httpx.WriteTimeout("x"), httpx.RemoteProtocolError("x"),
     response(200, stream=ChunkStream([b"a", b"b"], fail_after=1))],
)  # fmt: skip
def test_write_possibly_sent_maps_to_uncertain(reply) -> None:
    adapter = DemoAdapter(transport(Server(reply), Sleeps()))
    with pytest.raises(IntegrationWriteUncertainError) as caught:
        run(adapter.write())
    assert caught.value.effect_may_have_occurred is True
