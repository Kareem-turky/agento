from importlib.metadata import version

from agno.agent import Agent

from app.runtime import build_agent_runtime
from app.runtime.agno_runtime import SMOKE_TEST_AGENT_ID


def test_agno_is_the_pinned_external_dependency() -> None:
    import agno

    assert version("agno") == "3.0.11"
    assert "site-packages" in agno.__file__


def test_runtime_registers_smoke_test_agent_without_a_model() -> None:
    runtime = build_agent_runtime("test")

    agent = runtime.registry.get_agent(SMOKE_TEST_AGENT_ID)
    assert isinstance(agent, Agent)
    assert agent.model is None
    assert runtime.status()["registered_agents"] == [SMOKE_TEST_AGENT_ID]


def test_runtime_is_registered_on_app_startup(client) -> None:
    runtime = client.app.state.agent_runtime

    assert runtime.framework == "agno"
    assert runtime.registry.get_agent(SMOKE_TEST_AGENT_ID) is not None
