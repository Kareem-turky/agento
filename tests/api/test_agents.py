"""Agent definitions and environment-specific registration."""

import pytest
from agno.agent import Agent
from agno.models.anthropic.claude import Claude
from agno.models.openai.responses import OpenAIResponses

from app.agents.generic_reasoning import GENERIC_REASONING_AGENT_ID, build_generic_reasoning_agent
from app.main import create_app
from app.runtime import SMOKE_TEST_AGENT_ID, ModelConfigurationError
from tests.support.deterministic_model import DeterministicModel

DUMMY_KEY = "dummy-test-value-not-a-real-key"  # noqa: S105 - test fixture


def agent_ids(app) -> list[str]:
    return sorted(agent.id for agent in app.state.agent_os.agents)


def test_generic_agent_is_a_plain_agno_agent_without_tools_or_knowledge() -> None:
    model = DeterministicModel()
    agent = build_generic_reasoning_agent(model)

    assert isinstance(agent, Agent)
    assert agent.id == GENERIC_REASONING_AGENT_ID
    assert agent.model is model
    assert not agent.tools
    assert agent.knowledge is None
    assert agent.enable_agentic_memory is False
    assert agent.update_memory_on_run is False
    assert agent.search_knowledge is False


def test_generic_agent_instructions_state_its_boundaries() -> None:
    instructions = build_generic_reasoning_agent(DeterministicModel()).instructions
    text = " ".join(instructions).lower()

    # Answers come from the user's message only: no outside knowledge.
    assert instructions[0] == "Answer using only the information provided in the user's message."
    assert "general knowledge" not in text
    assert "do not introduce facts, figures or other knowledge" in text
    assert "that the user did not provide" in text
    assert "not contain enough information to answer, say so plainly" in text
    assert "no access to company data" in text
    assert "external systems" in text
    assert "no tools" in text
    assert "never claim to have performed an action" in text


@pytest.mark.parametrize("environment", ["local", "test"])
def test_development_without_model_registers_only_smoke_agent(
    settings, runtime_settings, environment
) -> None:
    app = create_app(settings.model_copy(update={"environment": environment}), runtime_settings)

    assert agent_ids(app) == [SMOKE_TEST_AGENT_ID]


@pytest.mark.parametrize("environment", ["local", "test"])
def test_development_with_model_registers_smoke_and_generic_agents(
    settings, runtime_settings, environment
) -> None:
    app = create_app(
        settings.model_copy(update={"environment": environment}),
        runtime_settings,
        default_model=DeterministicModel(),
    )

    assert agent_ids(app) == [GENERIC_REASONING_AGENT_ID, SMOKE_TEST_AGENT_ID]


@pytest.mark.parametrize("environment", ["staging", "production"])
def test_deployment_without_model_registers_no_agents(
    settings, runtime_settings, environment
) -> None:
    app = create_app(settings.model_copy(update={"environment": environment}), runtime_settings)

    assert agent_ids(app) == []


@pytest.mark.parametrize("environment", ["staging", "production"])
def test_deployment_with_model_registers_only_generic_agent(
    settings, runtime_settings, environment
) -> None:
    app = create_app(
        settings.model_copy(update={"environment": environment}),
        runtime_settings,
        default_model=DeterministicModel(),
    )

    assert agent_ids(app) == [GENERIC_REASONING_AGENT_ID]


@pytest.mark.parametrize(
    ("provider", "variable", "model_class"),
    [("openai", "OPENAI_API_KEY", OpenAIResponses), ("anthropic", "ANTHROPIC_API_KEY", Claude)],
)
def test_configured_provider_registers_generic_agent_with_native_model(
    monkeypatch: pytest.MonkeyPatch, settings, runtime_settings, provider, variable, model_class
) -> None:
    monkeypatch.setenv(variable, DUMMY_KEY)
    configured = settings.model_copy(
        update={
            "environment": "production",
            "default_model_provider": provider,
            "default_model_id": "example-model-id",
        }
    )

    app = create_app(configured, runtime_settings)
    (agent,) = app.state.agent_os.agents

    assert agent.id == GENERIC_REASONING_AGENT_ID
    assert type(agent.model) is model_class
    assert agent.model.id == "example-model-id"


def test_misconfigured_provider_stops_startup(
    monkeypatch: pytest.MonkeyPatch, settings, runtime_settings
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    configured = settings.model_copy(
        update={"default_model_provider": "openai", "default_model_id": "example-model-id"}
    )

    with pytest.raises(ModelConfigurationError, match="OPENAI_API_KEY"):
        create_app(configured, runtime_settings)


def test_health_does_not_expose_model_configuration(
    monkeypatch: pytest.MonkeyPatch, settings, runtime_settings
) -> None:
    from fastapi.testclient import TestClient

    monkeypatch.setenv("ANTHROPIC_API_KEY", DUMMY_KEY)
    configured = settings.model_copy(
        update={"default_model_provider": "anthropic", "default_model_id": "example-model-id"}
    )

    with TestClient(create_app(configured, runtime_settings)) as client:
        body = client.get("/health").text

    assert DUMMY_KEY not in body
    assert "example-model-id" not in body
    assert "anthropic" not in body.lower()
