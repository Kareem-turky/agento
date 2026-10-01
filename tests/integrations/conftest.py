"""The real PostgreSQL fixtures of the integration suite, for the native adapter's
conformance run (``APP_DATABASE_URL``, migrated to head; a hard failure in CI when
``REQUIRE_INTEGRATION_TESTS=1``, a skip otherwise). Only tests that request them use
them; the mock suites stay database-free."""

from tests.integration.conftest import (  # noqa: F401 - re-exported pytest fixtures
    database_url,
    engine,
    integration_settings,
    migrated,
)
