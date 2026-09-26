"""AgentOS + PostgresDb against a real PostgreSQL. No model is ever called."""

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from app.main import create_app
from app.runtime import SMOKE_TEST_AGENT_ID

pytestmark = pytest.mark.integration


def test_postgres_is_reachable(engine) -> None:
    with engine.connect() as connection:
        assert connection.execute(sa.text("SELECT 1")).scalar_one() == 1


def test_startup_provisions_agno_tables_in_agno_runtime_schema(client, engine, settings) -> None:
    schema = settings.agno_db_schema
    inspector = sa.inspect(engine)

    assert schema in inspector.get_schema_names()
    tables = set(inspector.get_table_names(schema=schema))
    assert {"agno_sessions", "agno_runs", "agno_schema_versions"} <= tables
    assert not any(
        table.startswith("agno_") for table in inspector.get_table_names(schema="public")
    )


def test_agentos_db_targets_the_configured_database(client, settings) -> None:
    db = client.app.state.agent_os.db

    assert db.db_schema == settings.agno_db_schema
    assert db.db_engine.url.database == settings.database_url.path.lstrip("/")


def test_session_persists_through_native_agentos_api(
    settings, runtime_settings, auth_headers, engine
) -> None:
    payload = {
        "agent_id": SMOKE_TEST_AGENT_ID,
        "session_name": "persistence-check",
        "session_state": {"probe": "value"},
    }

    with TestClient(create_app(settings, runtime_settings)) as first:
        created = first.post("/sessions?type=agent", json=payload, headers=auth_headers)
        assert created.status_code == 201, created.text
        session_id = created.json()["session_id"]

    try:
        # A brand-new application instance reads the session back: it came from PostgreSQL.
        with TestClient(create_app(settings, runtime_settings)) as second:
            fetched = second.get(f"/sessions/{session_id}?type=agent", headers=auth_headers)
            assert fetched.status_code == 200, fetched.text
            assert fetched.json()["session_name"] == "persistence-check"
            assert fetched.json()["session_state"] == {"probe": "value"}
            assert fetched.json()["agent_id"] == SMOKE_TEST_AGENT_ID

        with engine.connect() as connection:
            row = connection.execute(
                sa.text(
                    f'SELECT agent_id FROM "{settings.agno_db_schema}".agno_sessions '  # noqa: S608
                    "WHERE session_id = :session_id"
                ),
                {"session_id": session_id},
            ).one()
        assert row.agent_id == SMOKE_TEST_AGENT_ID
    finally:
        with TestClient(create_app(settings, runtime_settings)) as cleanup:
            cleanup.delete(f"/sessions/{session_id}?type=agent", headers=auth_headers)
