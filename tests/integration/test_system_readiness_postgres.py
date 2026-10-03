"""Task 039: readiness against a REAL PostgreSQL (outage, recovery, schema mismatch).

PostgreSQL itself is never stopped: the probe reaches it through a local TCP forwarder
that the test cuts and restores, so an outage and its recovery are real network events
for the SAME running probe and application (no rebuild, no restart).
"""

import asyncio
import socket
import threading
import time
from urllib.parse import urlsplit, urlunsplit

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from app.main import create_app
from app.persistence import PostgresReadinessProbe, create_readiness_engine
from app.system_operations import EXPECTED_PRODUCT_SCHEMA_REVISION
from app.system_operations import ComponentState as C
from tests.system_operations.test_system_api import READER_KEY, bearer, system_settings

pytestmark = pytest.mark.integration


class Forwarder:
    """A TCP forwarder on a fixed local port that can be cut and restored."""

    def __init__(self, target: tuple[str, int]) -> None:
        self.target = target
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            self.port = probe.getsockname()[1]
        self._listener: socket.socket | None = None
        self._open: list[socket.socket] = []
        self._lock = threading.Lock()

    def start(self) -> None:
        listener = socket.socket()
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", self.port))
        listener.listen(16)
        self._listener = listener
        threading.Thread(target=self._accept, args=(listener,), daemon=True).start()

    def _accept(self, listener: socket.socket) -> None:
        while True:
            try:
                client, _ = listener.accept()
            except OSError:
                return
            try:
                upstream = socket.create_connection(self.target, timeout=5)
            except OSError:
                client.close()
                continue
            with self._lock:
                self._open += [client, upstream]
            for source, sink in ((client, upstream), (upstream, client)):
                threading.Thread(target=self._pipe, args=(source, sink), daemon=True).start()

    @staticmethod
    def _pipe(source: socket.socket, sink: socket.socket) -> None:
        try:
            while chunk := source.recv(65536):
                sink.sendall(chunk)
        except OSError:
            pass
        finally:
            for s in (source, sink):
                try:
                    s.close()
                except OSError:
                    pass

    def cut(self) -> None:
        """PostgreSQL becomes unreachable: no listener, every open connection dropped."""
        if self._listener is not None:
            try:
                self._listener.shutdown(socket.SHUT_RDWR)  # wakes the blocked accept()
            except OSError:
                pass
            self._listener.close()
            self._listener = None
        with self._lock:
            for s in self._open:
                try:
                    s.shutdown(socket.SHUT_RDWR)
                    s.close()
                except OSError:
                    pass
            self._open.clear()


def via(database_url: str, port: int) -> str:
    parts = urlsplit(database_url)
    netloc = parts.netloc.rsplit("@", 1)
    host = f"127.0.0.1:{port}"
    return urlunsplit(parts._replace(netloc=f"{netloc[0]}@{host}" if len(netloc) == 2 else host))


@pytest.fixture
def forwarder(migrated: str):
    parts = urlsplit(migrated)
    running = Forwarder((parts.hostname or "127.0.0.1", parts.port or 5432))
    running.start()
    yield running
    running.cut()


def run(coroutine):
    return asyncio.run(coroutine)


async def check_once(url: str):
    engine = create_readiness_engine(url, timeout_seconds=2.0)
    try:
        return await PostgresReadinessProbe(
            engine, expected_revision=EXPECTED_PRODUCT_SCHEMA_REVISION
        ).check()
    finally:
        await engine.dispose()


def test_a_migrated_database_is_ready(migrated: str) -> None:
    result = run(check_once(migrated))
    assert (result.database, result.product_schema) == (C.READY, C.READY)


def test_outage_and_recovery_without_a_restart(
    migrated: str, forwarder, settings, runtime_settings
) -> None:
    engine = create_readiness_engine(via(migrated, forwarder.port), timeout_seconds=2.0)
    probe = PostgresReadinessProbe(engine, expected_revision=EXPECTED_PRODUCT_SCHEMA_REVISION)
    app = create_app(system_settings(settings), runtime_settings, system_probe=probe)
    with TestClient(app) as client:
        assert client.get("/health/ready").status_code == 200
        forwarder.cut()  # PostgreSQL unavailable
        started = time.monotonic()
        assert client.get("/health/ready").status_code == 503
        assert time.monotonic() - started < 5  # bounded, no hang
        assert client.get("/health/live").status_code == 200  # alive, not ready
        body = client.get("/api/v1/system/status", headers=bearer(READER_KEY)).json()
        assert body["components"]["database"] == "unavailable"
        assert body["reasons"] == ["database_unavailable", "schema_unavailable"]
        forwarder.start()  # PostgreSQL is back: the SAME app and probe recover
        assert client.get("/health/ready").status_code == 200
        body = client.get("/api/v1/system/status", headers=bearer(READER_KEY)).json()
        assert body["overall"] == "ready" and body["reasons"] == []


def test_schema_mismatch_is_not_ready_and_never_migrated(
    migrated: str, engine, settings, runtime_settings
) -> None:
    probe_engine = create_readiness_engine(migrated, timeout_seconds=2.0)
    probe = PostgresReadinessProbe(probe_engine, expected_revision=EXPECTED_PRODUCT_SCHEMA_REVISION)
    app = create_app(system_settings(settings), runtime_settings, system_probe=probe)
    try:
        with engine.begin() as connection:
            connection.execute(sa.text("UPDATE product.alembic_version SET version_num = '0007'"))
        with TestClient(app) as client:
            assert client.get("/health/ready").status_code == 503
            body = client.get("/api/v1/system/status", headers=bearer(READER_KEY)).json()
        assert body["components"]["product_schema"] == "mismatch"
        assert body["components"]["database"] == "ready"
        assert body["reasons"] == ["schema_mismatch"]
        with engine.connect() as connection:  # never auto-migrated
            assert (
                connection.execute(
                    sa.text("SELECT version_num FROM product.alembic_version")
                ).scalar_one()
                == "0007"
            )
    finally:
        with engine.begin() as connection:
            connection.execute(
                sa.text("UPDATE product.alembic_version SET version_num = :head"),
                {"head": EXPECTED_PRODUCT_SCHEMA_REVISION},
            )
    result = run(check_once(migrated))
    assert result.product_schema is C.READY


def test_a_silent_server_is_unavailable_within_the_bound(migrated: str) -> None:
    # Accepts TCP connections (kernel backlog) but never speaks the PostgreSQL protocol.
    with socket.socket() as silent:
        silent.bind(("127.0.0.1", 0))
        silent.listen(8)
        started = time.monotonic()
        result = run(check_once(via(migrated, silent.getsockname()[1])))
    assert result.database is C.UNAVAILABLE
    assert time.monotonic() - started < 4
