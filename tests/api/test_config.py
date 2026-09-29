import pytest
from pydantic import ValidationError

from app.config import Settings


def test_defaults_contain_no_connection_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    for variable in ("APP_DATABASE_URL", "APP_REDIS_URL", "APP_ENVIRONMENT"):
        monkeypatch.delenv(variable, raising=False)

    settings = Settings(_env_file=None)

    assert settings.environment == "local"
    assert settings.database_url is None
    assert settings.redis_url is None
    assert settings.agno_db_schema == "agno_runtime"


def test_loads_from_prefixed_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENVIRONMENT", "staging")
    monkeypatch.setenv("APP_API_PORT", "9000")
    monkeypatch.setenv("APP_DATABASE_URL", "postgresql+psycopg://user:pw@localhost:5432/db")
    monkeypatch.setenv("APP_REDIS_URL", "redis://localhost:6379/0")
    # Staging requires Product authentication (configured as a JSON array of hashes).
    monkeypatch.setenv("APP_PRODUCT_AUTH_MODE", "api_key")
    monkeypatch.setenv("APP_COMPANY_ID", "company-1")
    monkeypatch.setenv(
        "APP_PRODUCT_API_KEYS",
        '[{"key_id": "ops", "key_sha256": "' + "a" * 64 + '", "actor_id": "ops-api"}]',
    )

    settings = Settings(_env_file=None)

    assert settings.environment == "staging"
    assert settings.api_port == 9000
    assert settings.database_url is not None
    assert settings.database_url.hosts()[0]["host"] == "localhost"
    assert str(settings.redis_url) == "redis://localhost:6379/0"


def test_product_settings_ignore_agno_runtime_variables(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OS_SECURITY_KEY", "runtime-only")

    settings = Settings(_env_file=None)

    assert "runtime-only" not in settings.model_dump_json()


@pytest.mark.parametrize(
    ("variable", "value"),
    [
        ("APP_ENVIRONMENT", "unknown"),
        ("APP_API_PORT", "70000"),
        ("APP_DATABASE_URL", "mysql://localhost/db"),
        ("APP_REDIS_URL", "http://localhost:6379"),
        ("APP_AGNO_DB_SCHEMA", "bad-schema;drop"),
    ],
)
def test_rejects_invalid_values(monkeypatch: pytest.MonkeyPatch, variable: str, value: str) -> None:
    monkeypatch.setenv(variable, value)

    with pytest.raises(ValidationError):
        Settings(_env_file=None)
