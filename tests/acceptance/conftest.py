"""Acceptance fixtures: the SAME real PostgreSQL fixtures as the integration suite
(``APP_DATABASE_URL``, migrated to head with Alembic; a hard failure in CI when
``REQUIRE_INTEGRATION_TESTS=1``, a skip otherwise), plus the TEST-ONLY Product Core
installation harness (Task 040)."""

import socket

import pytest

from tests.integration.conftest import (  # noqa: F401 - re-exported pytest fixtures
    database_url,
    engine,
    integration_settings,
    migrated,
    settings,
)
from tests.support.product_core import core, secrets_dir  # noqa: F401 - pytest fixtures


@pytest.fixture(autouse=True)
def no_outbound_network(monkeypatch: pytest.MonkeyPatch) -> list[object]:
    """Any Python-level outbound connection (model, provider, collector, Internet) fails the
    acceptance test. PostgreSQL is reached through libpq, below this layer, and stays
    available; nothing else is needed. (The MVP module keeps its own identical guard.)"""
    attempts: list[object] = []

    def refuse(_socket: object, address: object) -> None:
        attempts.append(address)
        raise AssertionError(f"unexpected outbound connection to {address!r}")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", refuse)
    return attempts
