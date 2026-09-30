"""Safe, fixed transport errors for integration adapters.

Messages are fixed codes: never a URL, path, query, header, credential, body, response
or underlying exception text. Transport implementations raise them ``from None`` so no
chained exception can carry request details either.

``request_may_have_been_sent`` is the write-safety signal for the adapter above:

- ``False``: the request definitely did not leave this process (local validation,
  size limit, closed transport, no connection could be obtained or established);
- ``True``: transmission may have begun, so a mutating request may have taken effect.
  When in doubt the transport says ``True``.

The transport never maps these to business errors. An adapter decides, e.g.
``IntegrationWriteRejectedError`` only when ``request_may_have_been_sent`` is False and
``IntegrationWriteUncertainError`` otherwise. For reads the flag is not business
authority.
"""


class IntegrationHttpTransportError(Exception):
    """Base of every error the integration HTTP transport raises."""

    code = "integration_http_error"

    def __init__(self, *, request_may_have_been_sent: bool) -> None:
        super().__init__(self.code)
        self.request_may_have_been_sent = bool(request_may_have_been_sent)

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}(code={self.code!r}, "
            f"request_may_have_been_sent={self.request_may_have_been_sent})"
        )


class IntegrationHttpRequestInvalidError(IntegrationHttpTransportError):
    """The request failed local validation. Nothing was sent."""

    code = "integration_http_request_invalid"

    def __init__(self) -> None:
        super().__init__(request_may_have_been_sent=False)


class IntegrationHttpRequestTooLargeError(IntegrationHttpTransportError):
    """The request body exceeds the policy limit. Nothing was sent."""

    code = "integration_http_request_too_large"

    def __init__(self) -> None:
        super().__init__(request_may_have_been_sent=False)


class IntegrationHttpClosedError(IntegrationHttpTransportError):
    """The transport is closed. Nothing was sent."""

    code = "integration_http_closed"

    def __init__(self) -> None:
        super().__init__(request_may_have_been_sent=False)


class IntegrationHttpCloseError(IntegrationHttpTransportError):
    """Releasing the transport's resources failed. The transport is closed anyway; this
    is a lifecycle failure, never evidence that any request was sent."""

    code = "integration_http_close_failed"

    def __init__(self) -> None:
        super().__init__(request_may_have_been_sent=False)


class IntegrationHttpUnavailableError(IntegrationHttpTransportError):
    """The remote system could not be reached, timed out, or broke the exchange."""

    code = "integration_http_unavailable"


class IntegrationHttpResponseTooLargeError(IntegrationHttpTransportError):
    """The (decoded) response body exceeds the policy limit. The request was sent."""

    code = "integration_http_response_too_large"

    def __init__(self) -> None:
        super().__init__(request_may_have_been_sent=True)
