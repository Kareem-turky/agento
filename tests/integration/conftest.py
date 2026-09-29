import os

import pytest
import sqlalchemy as sa

from app.config import Settings

# CI sets this so that a missing/unreachable database fails the build instead of skipping.
REQUIRED = os.getenv("REQUIRE_INTEGRATION_TESTS") == "1"


@pytest.fixture(scope="session")
def integration_settings() -> Settings:
    database_url = os.getenv("APP_DATABASE_URL")
    if not database_url:
        message = "APP_DATABASE_URL is not set; integration tests need PostgreSQL"
        if REQUIRED:
            pytest.fail(message)
        pytest.skip(message)
    return Settings(_env_file=None, environment="test", database_url=database_url)


@pytest.fixture(scope="session")
def engine(integration_settings: Settings):
    engine = sa.create_engine(str(integration_settings.database_url))
    try:
        with engine.connect():
            pass
    except sa.exc.OperationalError as error:
        message = f"PostgreSQL is not reachable: {type(error).__name__}"
        if REQUIRED:
            pytest.fail(message)
        pytest.skip(message)
    yield engine
    engine.dispose()


@pytest.fixture
def settings(integration_settings: Settings) -> Settings:
    return integration_settings


@pytest.fixture(scope="session")
def database_url(integration_settings: Settings, engine) -> str:
    return str(integration_settings.database_url)


@pytest.fixture(scope="session")
def migrated(database_url: str, engine) -> str:
    """The product schema at the head revision, via Alembic (never create_all).

    CI already ran ``alembic upgrade head`` explicitly; this makes local runs work too.
    Upgrading an up-to-date database is a no-op.
    """
    from alembic import command

    from tests.integration.product_db import alembic_config

    command.upgrade(alembic_config(database_url), "head")
    return database_url
