"""Task 039: liveness, readiness and the Product-authenticated System Status."""

import socket

import pytest
from fastapi.testclient import TestClient

from app import __version__
from app.config import Settings
from app.main import create_app
from app.system_operations import ComponentState as C
from app.system_operations import DatabaseCheck
from tests.conftest import TEST_OS_SECURITY_KEY
from tests.support.observability import RecordingObservability
from tests.support.product_auth import (
    TEST_COMPANY_ID,
    TEST_PRODUCT_KEY,
    deployment_settings,
    principal,
    sha256_hex,
)

# Obviously test-only keys (long enough for the 32-character minimum).
READER_KEY = "test-system-reader-key-" + "b" * 32
COLLECTOR = "http://collector.internal.invalid:4318"
READY = DatabaseCheck(C.READY, C.READY)


class FakeProbe:
    def __init__(self, result: DatabaseCheck = READY) -> None:
        self.result = result
        self.calls = 0

    async def check(self) -> DatabaseCheck:
        self.calls += 1
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result


def system_settings(settings: Settings, **updates) -> Settings:
    reader = principal(
        READER_KEY,
        key_id="test-system-reader",
        actor_id="test-operator",
        permissions=frozenset({"system.read"}),
    )
    return deployment_settings(
        settings, environment="test", product_api_keys=(principal(), reader), **updates
    )


def bearer(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


@pytest.fixture
def probe() -> FakeProbe:
    return FakeProbe()


@pytest.fixture
def app(settings, runtime_settings, probe):
    return create_app(system_settings(settings), runtime_settings, system_probe=probe)


# ----- liveness ----------------------------------------------------------------------------------


def test_liveness_is_dependency_free_and_minimal(app, probe) -> None:
    with TestClient(app) as client:
        response = client.get("/health/live")
    assert response.status_code == 200 and response.json() == {"status": "alive"}
    assert response.headers["cache-control"] == "no-store"
    assert probe.calls == 0  # no database query


def test_liveness_stays_200_while_the_database_is_down(settings, runtime_settings) -> None:
    down = FakeProbe(DatabaseCheck(C.UNAVAILABLE, C.UNAVAILABLE))
    app = create_app(system_settings(settings), runtime_settings, system_probe=down)
    with TestClient(app) as client:
        assert client.get("/health/live").status_code == 200
        ready = client.get("/health/ready")
    assert ready.status_code == 503 and ready.json() == {"status": "not_ready"}


# ----- readiness ---------------------------------------------------------------------------------


def test_readiness_needs_the_started_lifespan_and_runtime(app, probe) -> None:
    client = TestClient(app)  # no lifespan: the application has not started serving
    response = client.get("/health/ready")
    assert response.status_code == 503 and response.json() == {"status": "not_ready"}
    assert probe.calls == 0  # not serving yet: no database round trip
    with TestClient(app) as started:
        response = started.get("/health/ready")
    assert response.status_code == 200 and response.json() == {"status": "ready"}
    assert response.headers["cache-control"] == "no-store"
    assert probe.calls == 1


@pytest.mark.parametrize(
    "check",
    [
        DatabaseCheck(C.UNAVAILABLE, C.UNAVAILABLE),
        DatabaseCheck(C.READY, C.MISMATCH),
        DatabaseCheck(C.READY, C.UNAVAILABLE),
        RuntimeError("probe defect"),
    ],
)
def test_readiness_fails_closed(settings, runtime_settings, check) -> None:
    app = create_app(system_settings(settings), runtime_settings, system_probe=FakeProbe(check))
    with TestClient(app) as client:
        response = client.get("/health/ready")
    assert response.status_code == 503 and response.json() == {"status": "not_ready"}


def test_readiness_without_a_probe_is_never_ready(settings, runtime_settings) -> None:
    with TestClient(create_app(system_settings(settings), runtime_settings)) as client:
        assert client.get("/health/ready").status_code == 503
        assert client.get("/health/live").status_code == 200


def test_readiness_is_evaluated_fresh_and_recovers_without_a_restart(app, probe) -> None:
    with TestClient(app) as client:
        assert client.get("/health/ready").status_code == 200
        probe.result = DatabaseCheck(C.UNAVAILABLE, C.UNAVAILABLE)  # PostgreSQL goes away
        assert client.get("/health/ready").status_code == 503
        assert client.get("/health/live").status_code == 200
        probe.result = READY  # ... and comes back: the SAME process recovers
        assert client.get("/health/ready").status_code == 200
    assert probe.calls == 3


def test_public_health_discloses_nothing(settings, runtime_settings) -> None:
    for probe in (FakeProbe(), FakeProbe(DatabaseCheck(C.READY, C.MISMATCH))):
        app = create_app(system_settings(settings), runtime_settings, system_probe=probe)
        with TestClient(app) as client:
            for path in ("/health/live", "/health/ready"):
                response = client.get(path)
                text = response.text.lower()
                assert set(response.json()) == {"status"}
                for word in (
                    __version__,
                    "test",
                    "agno",
                    "fastapi",
                    "0008",
                    "mismatch",
                    "schema",
                    "database",
                    "127.0.0.1",
                    "postgres",
                    socket.gethostname().lower(),
                    TEST_COMPANY_ID,
                    "version",
                    "environment",
                ):
                    assert word.lower() not in text, (path, word)


def test_public_health_needs_no_credential_and_ignores_one(app) -> None:
    with TestClient(app) as client:
        for headers in (
            {},
            bearer(TEST_OS_SECURITY_KEY),
            bearer(TEST_PRODUCT_KEY),
            bearer("nonsense"),
        ):
            assert client.get("/health/live", headers=headers).status_code == 200
            assert client.get("/health/ready", headers=headers).status_code == 200


def test_legacy_health_is_unchanged(app) -> None:
    with TestClient(app) as client:
        body = client.get("/health").json()
    assert set(body) == {"status", "application", "agent_runtime"}
    assert body["status"] == "ok" and body["application"]["version"] == __version__
    assert set(body["agent_runtime"]) == {"framework", "version", "status"}


# ----- System Status ----------------------------------------------------------------------------


def test_system_status_requires_product_auth_and_system_read(app) -> None:
    with TestClient(app) as client:
        assert client.get("/api/v1/system/status").status_code == 401
        assert (
            client.get("/api/v1/system/status", headers=bearer(TEST_OS_SECURITY_KEY)).status_code
            == 401
        )
        assert client.get("/api/v1/system/status", headers=bearer("x" * 40)).status_code == 401
        assert (
            client.get("/api/v1/system/status", headers=bearer(TEST_PRODUCT_KEY)).status_code == 403
        )
        response = client.get("/api/v1/system/status", headers=bearer(READER_KEY))
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"


def test_system_status_has_exactly_the_safe_shape(settings, runtime_settings, app) -> None:
    with TestClient(app) as client:
        response = client.get("/api/v1/system/status", headers=bearer(READER_KEY))
    body = response.json()
    assert set(body) == {
        "request_id",
        "application",
        "overall",
        "reasons",
        "components",
        "observability",
    }
    assert body["request_id"] == response.headers["x-request-id"]
    assert set(body["application"]) == {"version", "environment", "uptime_seconds"}
    assert body["application"]["version"] == __version__
    assert body["application"]["environment"] == "test"
    assert isinstance(body["application"]["uptime_seconds"], int)
    assert body["overall"] == "ready" and body["reasons"] == []
    assert body["components"] == {
        "application": "ready",
        "database": "ready",
        "product_schema": "ready",
        "agent_runtime": "ready",
    }
    assert body["observability"] == {"export_mode": "disabled"}
    text = response.text
    configured = system_settings(settings)
    for forbidden in (
        str(configured.database_url),
        "127.0.0.1",
        "postgres",
        TEST_COMPANY_ID,
        "test-operator",
        "test-system-reader",
        sha256_hex(READER_KEY),
        READER_KEY,
        TEST_OS_SECURITY_KEY,
        "agno",
        "/app",
        "Traceback",
        "0008",
        "store",
        "actor",
        "company",
        "key",
    ):
        assert forbidden not in text, forbidden


def test_system_status_explains_not_ready_with_stable_codes(settings, runtime_settings) -> None:
    probe = FakeProbe(DatabaseCheck(C.READY, C.MISMATCH))
    app = create_app(system_settings(settings), runtime_settings, system_probe=probe)
    with TestClient(app) as client:
        body = client.get("/api/v1/system/status", headers=bearer(READER_KEY)).json()
        assert body["overall"] == "not_ready" and body["reasons"] == ["schema_mismatch"]
        assert body["components"]["product_schema"] == "mismatch"
        probe.result = DatabaseCheck(C.UNAVAILABLE, C.UNAVAILABLE)
        body = client.get("/api/v1/system/status", headers=bearer(READER_KEY)).json()
        assert body["reasons"] == ["database_unavailable", "schema_unavailable"]
        assert body["components"]["database"] == "unavailable"
        probe.result = RuntimeError("password=hunter2 host=10.0.0.5")
        response = client.get("/api/v1/system/status", headers=bearer(READER_KEY))
    assert (
        response.status_code == 200 and response.json()["components"]["database"] == "unavailable"
    )
    assert "hunter2" not in response.text and "10.0.0.5" not in response.text


def test_system_status_reports_the_export_mode_never_the_endpoint(
    settings, runtime_settings
) -> None:
    configured = system_settings(
        settings, otel_export_mode="otlp_http", otel_export_endpoint=COLLECTOR
    )
    app = create_app(
        configured,
        runtime_settings,
        system_probe=FakeProbe(),
        observability=RecordingObservability(),
    )
    with TestClient(app) as client:
        response = client.get("/api/v1/system/status", headers=bearer(READER_KEY))
    assert response.json()["observability"] == {"export_mode": "otlp_http"}
    assert "collector" not in response.text and "4318" not in response.text


def test_system_status_without_a_database_reports_unavailable(settings, runtime_settings) -> None:
    with TestClient(create_app(system_settings(settings), runtime_settings)) as client:
        body = client.get("/api/v1/system/status", headers=bearer(READER_KEY)).json()
    assert body["overall"] == "not_ready"
    assert body["components"]["database"] == body["components"]["product_schema"] == "unavailable"


def test_system_status_is_observed_with_a_bounded_label(settings, runtime_settings) -> None:
    from app.observability import ObservationOutcome, ProductOperation

    recorder = RecordingObservability()
    app = create_app(
        system_settings(settings),
        runtime_settings,
        system_probe=FakeProbe(),
        observability=recorder,
    )
    with TestClient(app) as client:
        client.get("/api/v1/system/status", headers=bearer(READER_KEY))
        client.get("/api/v1/system/status", headers=bearer(TEST_PRODUCT_KEY))
    records = recorder.of(ProductOperation.SYSTEM_STATUS)
    assert [r.outcome for r in records] == [ObservationOutcome.COMPLETED, ObservationOutcome.DENIED]
    assert records[0].attributes == {"business_status": "ready"}


def test_health_and_status_make_no_network_model_or_integration_call(
    monkeypatch: pytest.MonkeyPatch, app
) -> None:
    def refuse(*_args, **_kwargs):
        raise AssertionError("no network call is allowed here")

    with TestClient(app) as client:
        monkeypatch.setattr(socket.socket, "connect", refuse)
        monkeypatch.setattr(socket, "create_connection", refuse)
        assert client.get("/health/live").status_code == 200
        assert client.get("/health/ready").status_code == 200
        assert client.get("/api/v1/system/status", headers=bearer(READER_KEY)).status_code == 200


def test_there_is_no_system_write_route(app) -> None:
    from tests.routes import effective_api_routes

    routes = effective_api_routes(app.routes)
    system = sorted((m, p) for m, p in routes if p.startswith(("/api/v1/system", "/health")))
    assert system == [
        ("GET", "/api/v1/system/status"),
        ("GET", "/health"),
        ("GET", "/health/live"),
        ("GET", "/health/ready"),
    ]
    paths = {p for _, p in routes}
    for forbidden in (
        "/logs",
        "/api/v1/logs",
        "/api/v1/metrics",
        "/api/v1/system/restart",
        "/api/v1/system/backup",
        "/api/v1/system/restore",
        "/api/v1/audit",
    ):
        assert forbidden not in paths
    # No Product-owned metrics or log route (AgentOS's own pre-existing runtime routes stay
    # behind the AgentOS key and are not Product routes).
    from pathlib import Path

    routes_dir = Path(__file__).resolve().parents[2] / "apps" / "api" / "app" / "routes"
    for path in routes_dir.glob("*.py"):
        for word in ('"/metrics', '"/logs', '"/api/v1/metrics', '"/api/v1/logs'):
            assert word not in path.read_text(), (path.name, word)
    with TestClient(app) as client:
        assert client.get("/metrics").status_code == 401
