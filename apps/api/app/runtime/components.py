"""Builds the agents registered with AgentOS.

settings -> default model -> agents -> ``AgentOS(agents=...)``

* ``runtime-smoke-test`` (non-executing) is registered only in local/test.
* ``generic-reasoning`` is registered whenever a default model is available.
"""

from agno.agent import Agent
from agno.models.base import Model

from app.agents.generic_reasoning import build_generic_reasoning_agent
from app.config import Settings
from app.runtime.non_executing_model import NonExecutingModel

SMOKE_TEST_AGENT_ID = "runtime-smoke-test"


def build_smoke_test_agent() -> Agent:
    return Agent(
        id=SMOKE_TEST_AGENT_ID,
        name="Runtime smoke test",
        model=NonExecutingModel(),
        description="Non-production agent used only to verify AgentOS registration. Never run.",
    )


def build_agents(settings: Settings, default_model: Model | None) -> list[Agent]:
    agents: list[Agent] = []
    if settings.is_development:
        agents.append(build_smoke_test_agent())
    if default_model is not None:
        agents.append(build_generic_reasoning_agent(default_model))
    return agents
