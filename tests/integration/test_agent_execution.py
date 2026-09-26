"""Real Agno agent execution through the native AgentOS run API.

``generic-reasoning`` runs on the TEST-ONLY deterministic model, so no model
provider is ever contacted. A guard makes any Python-level outbound connection fail
the test (PostgreSQL is reached through libpq, which the guard does not intercept).
"""

import socket

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from app.agents.generic_reasoning import GENERIC_REASONING_AGENT_ID
from app.main import create_app
from tests.support.deterministic_model import DETERMINISTIC_RESPONSE, DeterministicModel

pytestmark = pytest.mark.integration

PROVIDER_KEY_VARIABLES = ("OPENAI_API_KEY", "ANTHROPIC_API_KEY")


@pytest.fixture
def no_outbound_network(monkeypatch: pytest.MonkeyPatch) -> list[object]:
    attempts: list[object] = []

    def refuse(self, address, *args, **kwargs):
        attempts.append(address)
        raise AssertionError(f"Unexpected outbound network connection to {address!r}")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", refuse)
    for variable in PROVIDER_KEY_VARIABLES:
        monkeypatch.delenv(variable, raising=False)
    return attempts


def test_generic_agent_runs_through_agentos_and_persists(
    settings, runtime_settings, auth_headers, engine, no_outbound_network
) -> None:
    model = DeterministicModel()
    run_url = f"/agents/{GENERIC_REASONING_AGENT_ID}/runs"

    with TestClient(create_app(settings, runtime_settings, default_model=model)) as client:
        response = client.post(
            run_url,
            headers=auth_headers,
            data={"message": "What is two plus two?", "stream": "false"},
        )

    assert response.status_code == 200, response.text
    run = response.json()
    assert run["content"] == DETERMINISTIC_RESPONSE
    assert run["agent_id"] == GENERIC_REASONING_AGENT_ID
    assert run["status"] == "COMPLETED"
    assert run["model"] == model.id
    session_id, run_id = run["session_id"], run["run_id"]

    # Exactly our model was invoked, and nothing tried to reach the network.
    assert model.calls == ["ainvoke"]
    assert no_outbound_network == []

    try:
        # A fresh application instance reads the run back through native AgentOS APIs.
        with TestClient(create_app(settings, runtime_settings, default_model=model)) as client:
            runs = client.get(f"{run_url}?session_id={session_id}", headers=auth_headers)
            session = client.get(f"/sessions/{session_id}?type=agent", headers=auth_headers)

        assert runs.status_code == 200, runs.text
        (stored_run,) = runs.json()
        assert stored_run["run_id"] == run_id
        assert stored_run["run_input"] == "What is two plus two?"
        assert stored_run["content"] == DETERMINISTIC_RESPONSE
        assert session.status_code == 200, session.text
        assert session.json()["agent_id"] == GENERIC_REASONING_AGENT_ID

        # And the rows are in Agno's own tables in the agno_runtime schema.
        schema = settings.agno_db_schema
        with engine.connect() as connection:
            session_row = connection.execute(
                sa.text(f'SELECT agent_id FROM "{schema}".agno_sessions WHERE session_id = :id'),  # noqa: S608
                {"id": session_id},
            ).one()
            run_row = connection.execute(
                sa.text(
                    f'SELECT session_id, agent_id, status FROM "{schema}".agno_runs '  # noqa: S608
                    "WHERE run_id = :id"
                ),
                {"id": run_id},
            ).one()
        assert session_row.agent_id == GENERIC_REASONING_AGENT_ID
        assert run_row.session_id == session_id
        assert run_row.agent_id == GENERIC_REASONING_AGENT_ID
        assert run_row.status == "COMPLETED"
    finally:
        with TestClient(create_app(settings, runtime_settings)) as cleanup:
            cleanup.delete(f"/sessions/{session_id}?type=agent", headers=auth_headers)


def test_run_requires_the_security_key(settings, runtime_settings, no_outbound_network) -> None:
    model = DeterministicModel()

    with TestClient(create_app(settings, runtime_settings, default_model=model)) as client:
        response = client.post(
            f"/agents/{GENERIC_REASONING_AGENT_ID}/runs",
            data={"message": "hello", "stream": "false"},
        )

    assert response.status_code == 401
    assert model.calls == []


def test_network_guard_blocks_outbound_connections(no_outbound_network) -> None:
    with pytest.raises(AssertionError, match="Unexpected outbound network connection"):
        socket.create_connection(("127.0.0.1", 9), timeout=1)
    assert no_outbound_network == [("127.0.0.1", 9)]
