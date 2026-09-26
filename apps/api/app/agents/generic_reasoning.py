"""The ``generic-reasoning`` agent.

Not a business agent: it exists to prove the real model execution path. It has no
tools, knowledge, memory or access to any system, and its instructions keep it from
claiming otherwise.
"""

from agno.agent import Agent
from agno.models.base import Model

GENERIC_REASONING_AGENT_ID = "generic-reasoning"

INSTRUCTIONS = [
    "Answer using only the information provided in the user's message.",
    "Do not introduce facts, figures or other knowledge that the user did not provide. "
    "If the message does not contain enough information to answer, say so plainly.",
    "You have no access to company data, databases, files or external systems. "
    "Never claim or imply that you do.",
    "You have no tools and cannot take actions. Never claim to have performed an action.",
    "If a request needs data, tools or actions you do not have, say so plainly.",
]


def build_generic_reasoning_agent(model: Model) -> Agent:
    return Agent(
        id=GENERIC_REASONING_AGENT_ID,
        name="Generic reasoning",
        description="Generic agent that answers from the user's message only.",
        model=model,
        instructions=INSTRUCTIONS,
        tools=[],
        # No memory or knowledge behaviour.
        enable_agentic_memory=False,
        update_memory_on_run=False,
        add_memories_to_context=False,
        search_knowledge=False,
        read_chat_history=False,
    )
