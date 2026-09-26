"""Agno telemetry is disabled by product policy, fail closed."""

import asyncio

import pytest
from agno.agent import Agent

from app.agents.generic_reasoning import GENERIC_REASONING_AGENT_ID, build_generic_reasoning_agent
from app.main import create_app
from app.runtime import SMOKE_TEST_AGENT_ID, RuntimeConfigurationError, enforce_telemetry_policy
from app.runtime.components import build_smoke_test_agent
from tests.support.deterministic_model import DeterministicModel

POLICY_MESSAGE = "Agno telemetry is disabled by product policy"


def agents_by_id(app) -> dict[str, Agent]:
    return {agent.id: agent for agent in app.state.agent_os.agents}


def test_product_agents_are_built_with_telemetry_off() -> None:
    assert build_smoke_test_agent().telemetry is False
    assert build_generic_reasoning_agent(DeterministicModel()).telemetry is False


@pytest.mark.parametrize("value", [None, "false"])
def test_boots_with_telemetry_unset_or_false(
    monkeypatch: pytest.MonkeyPatch, settings, runtime_settings, value
) -> None:
    if value is None:
        monkeypatch.delenv("AGNO_TELEMETRY", raising=False)
    else:
        monkeypatch.setenv("AGNO_TELEMETRY", value)

    app = create_app(settings, runtime_settings, default_model=DeterministicModel())
    agents = agents_by_id(app)

    assert app.state.agent_os.telemetry is False
    assert agents[SMOKE_TEST_AGENT_ID].telemetry is False
    assert agents[GENERIC_REASONING_AGENT_ID].telemetry is False


@pytest.mark.parametrize("value", ["true", "TRUE", "True", " true "])
def test_enabling_telemetry_stops_startup(
    monkeypatch: pytest.MonkeyPatch, settings, runtime_settings, value
) -> None:
    monkeypatch.setenv("AGNO_TELEMETRY", value)

    with pytest.raises(RuntimeConfigurationError, match=POLICY_MESSAGE):
        create_app(settings, runtime_settings)


@pytest.mark.parametrize("value", ["yes", "1", "0", "on", "off", "enabled", "no", "", "   "])
def test_ambiguous_values_are_rejected(
    monkeypatch: pytest.MonkeyPatch, settings, runtime_settings, value
) -> None:
    monkeypatch.setenv("AGNO_TELEMETRY", value)

    with pytest.raises(RuntimeConfigurationError, match=POLICY_MESSAGE):
        create_app(settings, runtime_settings)


@pytest.mark.parametrize("value", ["false", "FALSE", " False "])
def test_policy_accepts_false_in_any_case(value) -> None:
    enforce_telemetry_policy({"AGNO_TELEMETRY": value})


def test_policy_accepts_unset() -> None:
    enforce_telemetry_policy({})


def test_error_does_not_echo_the_configured_value() -> None:
    with pytest.raises(RuntimeConfigurationError) as error:
        enforce_telemetry_policy({"AGNO_TELEMETRY": "enabled-by-someone"})

    assert "enabled-by-someone" not in str(error.value)
    assert "must be unset or 'false'" in str(error.value)


def test_telemetry_spy_detects_real_agno_telemetry(
    monkeypatch: pytest.MonkeyPatch, telemetry_calls
) -> None:
    """Positive control: an Agno agent with telemetry on is caught by the spy."""
    monkeypatch.delenv("AGNO_TELEMETRY", raising=False)
    agent = Agent(id="control", model=DeterministicModel(), telemetry=True)

    asyncio.run(agent.arun("hello"))

    assert telemetry_calls, "the spy must see telemetry from a telemetry-enabled agent"


def test_generic_agent_run_sends_no_telemetry_with_env_unset(
    monkeypatch: pytest.MonkeyPatch, telemetry_calls
) -> None:
    monkeypatch.delenv("AGNO_TELEMETRY", raising=False)
    agent = build_generic_reasoning_agent(DeterministicModel())

    asyncio.run(agent.arun("hello"))
    agent.run("hello again")

    assert telemetry_calls == []
    assert agent.telemetry is False
