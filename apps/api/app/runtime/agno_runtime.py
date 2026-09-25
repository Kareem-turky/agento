"""Agno runtime registration.

Agno is used as an external, pinned dependency. This module only wires an
Agno ``Registry`` into the application lifecycle. It registers a single
model-less smoke-test agent to prove the runtime is importable and
constructible; that agent is never executed and makes no model calls.
"""

from dataclasses import dataclass
from importlib.metadata import version

from agno.agent import Agent
from agno.registry import Registry

SMOKE_TEST_AGENT_ID = "runtime-smoke-test"


@dataclass(frozen=True)
class AgentRuntime:
    framework: str
    framework_version: str
    registry: Registry

    def status(self) -> dict[str, object]:
        return {
            "framework": self.framework,
            "version": self.framework_version,
            "registered_agents": sorted(self.registry.get_agent_ids()),
        }


def build_agent_runtime(name: str) -> AgentRuntime:
    smoke_test_agent = Agent(
        id=SMOKE_TEST_AGENT_ID,
        name=SMOKE_TEST_AGENT_ID,
        description="Non-executing agent used only to verify runtime registration.",
    )
    registry = Registry(name=f"{name}-registry", agents=[smoke_test_agent])
    return AgentRuntime(framework="agno", framework_version=version("agno"), registry=registry)
