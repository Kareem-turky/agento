"""Secure, provider-neutral outbound HTTP transport for integration adapters.

    Provider adapter -> IntegrationHttpTransport -> trusted external API origin

Infrastructure only: no provider, credentials or configuration live here, and nothing
in the Product is wired to it yet. Only reviewed provider adapters may use it; agents,
workflows and models never receive a transport.
"""

from app.integrations.http.contracts import (
    FORBIDDEN_REQUEST_HEADERS,
    HttpMethod,
    IntegrationHttpRequest,
    IntegrationHttpResponse,
    IntegrationHttpTransport,
    ResponseHeaders,
)
from app.integrations.http.errors import (
    IntegrationHttpClosedError,
    IntegrationHttpCloseError,
    IntegrationHttpRequestInvalidError,
    IntegrationHttpRequestTooLargeError,
    IntegrationHttpResponseTooLargeError,
    IntegrationHttpTransportError,
    IntegrationHttpUnavailableError,
)
from app.integrations.http.httpx_transport import HttpxIntegrationTransport
from app.integrations.http.policy import IntegrationHttpPolicy

__all__ = [
    "FORBIDDEN_REQUEST_HEADERS",
    "HttpMethod",
    "HttpxIntegrationTransport",
    "IntegrationHttpCloseError",
    "IntegrationHttpClosedError",
    "IntegrationHttpPolicy",
    "IntegrationHttpRequest",
    "IntegrationHttpRequestInvalidError",
    "IntegrationHttpRequestTooLargeError",
    "IntegrationHttpResponse",
    "IntegrationHttpResponseTooLargeError",
    "IntegrationHttpTransport",
    "IntegrationHttpTransportError",
    "IntegrationHttpUnavailableError",
    "ResponseHeaders",
]
