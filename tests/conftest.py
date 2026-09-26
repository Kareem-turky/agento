import pytest
from agno.os.settings import AgnoAPISettings
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app

# Test fixture, not a secret. Satisfies the 32-character minimum.
TEST_OS_SECURITY_KEY = "test-only-agentos-security-key-000000"  # noqa: S105

# Unit tests never reach a database: this address refuses connections immediately,
# and AgentOS only logs a warning when it cannot provision tables at startup.
UNREACHABLE_DATABASE_URL = "postgresql+psycopg://unit@127.0.0.1:1/unit"


@pytest.fixture
def settings() -> Settings:
    # Ignore any developer .env so tests are deterministic.
    return Settings(_env_file=None, environment="test", database_url=UNREACHABLE_DATABASE_URL)


@pytest.fixture
def runtime_settings() -> AgnoAPISettings:
    return AgnoAPISettings(os_security_key=TEST_OS_SECURITY_KEY)


@pytest.fixture
def auth_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {TEST_OS_SECURITY_KEY}"}


@pytest.fixture
def client(settings: Settings, runtime_settings: AgnoAPISettings):
    with TestClient(create_app(settings, runtime_settings)) as test_client:
        yield test_client


@pytest.fixture
def telemetry_calls(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, dict]]:
    """Record every Agno telemetry dispatch (all Agno telemetry funnels through here)."""
    from agno.api.api import api

    calls: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        api, "post_in_background", lambda route, payload: calls.append((route, payload))
    )
    return calls
