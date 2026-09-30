"""Defensive suppression of HTTP-dependency log records during integration requests.

httpx logs every request at INFO ("HTTP Request: METHOD <full URL> ...") and httpcore
logs connection and protocol details at DEBUG. For integration traffic those records
would carry the origin, path, query (entity identifiers, possibly credentials) or raw
failure text, and would be emitted whatever log level an operator enables.

Mechanism (stdlib only, concurrency-safe):

- ``_IN_TRANSPORT`` is a ContextVar set only while the secure transport sends a
  request, streams its response or closes its client (``suppressed()``). Each asyncio
  task has its own context, so concurrent code outside the transport is unaffected.
- One shared, stateless ``logging.Filter`` is attached to the ``httpx`` and ``httpcore``
  loggers and each of their module loggers (a logger's filters apply only to records
  created on that logger, so every emitting logger needs it). It drops a record only
  while ``_IN_TRANSPORT`` is set; outside the transport, dependency logging is untouched.

No logger level, ``disabled`` or ``propagate`` flag is ever changed, the root logger is
never touched, nothing is logged, and the filter stores no request data. Installation
is idempotent: the same filter instance is attached at most once per logger.
"""

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

# Every logger httpx 0.28 / httpcore 1.x emit on (guarded by a test that scans them).
DEPENDENCY_LOGGERS = (
    "httpx",
    "httpcore",
    "httpcore.connection",
    "httpcore.http11",
    "httpcore.http2",
    "httpcore.proxy",
    "httpcore.socks",
)
_DEPENDENCY_ROOTS = ("httpx", "httpcore")

_IN_TRANSPORT: ContextVar[bool] = ContextVar("integration_http_transport_active", default=False)


class _SuppressInsideTransport(logging.Filter):
    """Drops a dependency record only when it is emitted inside the secure transport."""

    def filter(self, record: logging.LogRecord) -> bool:
        return not _IN_TRANSPORT.get()


SUPPRESSION_FILTER = _SuppressInsideTransport()


def install_dependency_log_suppression() -> None:
    """Attach the shared filter to every dependency logger (idempotent, cheap)."""
    names: set[str] = set(DEPENDENCY_LOGGERS)
    names.update(
        name
        for name in tuple(logging.Logger.manager.loggerDict)
        if name.split(".", 1)[0] in _DEPENDENCY_ROOTS
    )
    for name in names:
        logger = logging.getLogger(name)
        if SUPPRESSION_FILTER not in logger.filters:
            logger.addFilter(SUPPRESSION_FILTER)


@contextmanager
def suppressed() -> Iterator[None]:
    """Mark the current task as inside the secure transport; always restored."""
    token = _IN_TRANSPORT.set(True)
    try:
        yield
    finally:
        _IN_TRANSPORT.reset(token)


def suppression_active() -> bool:
    return _IN_TRANSPORT.get()
