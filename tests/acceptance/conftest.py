"""MVP acceptance fixtures: the SAME real PostgreSQL fixtures as the integration suite
(``APP_DATABASE_URL``, migrated to head with Alembic; a hard failure in CI when
``REQUIRE_INTEGRATION_TESTS=1``, a skip otherwise)."""

from tests.integration.conftest import (  # noqa: F401 - re-exported pytest fixtures
    database_url,
    engine,
    integration_settings,
    migrated,
    settings,
)
