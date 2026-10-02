"""Product-owned Agent definitions and manifests (immutable, descriptive metadata).

An ``AgentDefinition`` describes an Agent TYPE installed in this build: identity, name,
category, lifecycle, the definition-level default enabled state, capabilities and its
``AgentManifest``. It is plain data: it names no Python class, module or import path,
holds no instructions, model, credential or provider, and is never built from stored
values. The runtime Agent itself is built only by trusted Product code
(``app.agents``); a definition never constructs it.

The manifest is DESCRIPTIVE security/runtime metadata (tools, governed actions,
tool-call limit, Product domain requirements, safety properties). It is not an
authorization authority: every tool call is still decided by trusted actor ->
GovernanceGate -> policy -> ExecutionCoordinator -> verification -> audit.
"""

from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

_FROZEN = ConfigDict(frozen=True, extra="forbid")

AgentId = Annotated[str, StringConstraints(strict=True, pattern=r"^[a-z][a-z0-9-]{0,62}[a-z0-9]$")]
# Dotted lowercase identifiers: capabilities, Product action names, domain requirements.
_DOTTED = r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$"
DottedId = Annotated[str, StringConstraints(strict=True, max_length=128, pattern=_DOTTED)]
ToolId = Annotated[str, StringConstraints(strict=True, pattern=r"^[a-z][a-z0-9_]{0,63}$")]
Text = Annotated[str, StringConstraints(strict=True, min_length=1, max_length=500)]


class AgentCategory(StrEnum):
    """Classification only: a category grants nothing and selects no behaviour."""

    OPERATIONS = "operations"


class AgentLifecycle(StrEnum):
    ACTIVE = "active"
    PREVIEW = "preview"
    DEPRECATED = "deprecated"


class AgentToolAccess(StrEnum):
    READ = "read"
    WRITE = "write"


class AgentSafetyProperty(StrEnum):
    """Fixed vocabulary of runtime safety facts an Agent's trusted code guarantees."""

    TRUSTED_RUN_CONTEXT = "trusted_run_context"
    STORE_SCOPED = "store_scoped"
    GOVERNED_TOOLS = "governed_tools"
    WRITE_INTENT_REQUIRED = "write_intent_required"
    UNTRUSTED_MODEL = "untrusted_model"
    TOOL_OUTPUT_UNTRUSTED = "tool_output_untrusted"
    PRODUCT_RUN_READ_ONLY = "product_run_read_only"
    NO_MEMORY_KNOWLEDGE_OR_HISTORY = "no_memory_knowledge_or_history"
    NOT_EXPOSED_THROUGH_AGENTOS = "not_exposed_through_agentos"


class AgentToolDeclaration(BaseModel):
    """One Product tool of the Agent and the governed Product actions behind it."""

    model_config = _FROZEN

    tool_id: ToolId
    access: AgentToolAccess
    action_names: tuple[DottedId, ...] = Field(min_length=1)
    description: Text


class AgentManifest(BaseModel):
    model_config = _FROZEN

    tools: tuple[AgentToolDeclaration, ...] = ()
    tool_call_limit: int = Field(ge=1, le=50)
    # Product domain capabilities the Agent needs (never a provider name).
    requirements: frozenset[DottedId] = frozenset()
    safety: frozenset[AgentSafetyProperty] = frozenset()

    @model_validator(mode="after")
    def _consistent(self) -> "AgentManifest":
        ids = [t.tool_id for t in self.tools]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate tool id")
        writes = any(t.access is AgentToolAccess.WRITE for t in self.tools)
        if writes and AgentSafetyProperty.WRITE_INTENT_REQUIRED not in self.safety:
            raise ValueError("a write tool requires the write_intent_required property")
        return self

    @property
    def action_names(self) -> frozenset[str]:
        return frozenset(name for tool in self.tools for name in tool.action_names)


class AgentDefinition(BaseModel):
    model_config = _FROZEN

    agent_id: AgentId
    name: Annotated[str, StringConstraints(strict=True, min_length=1, max_length=80)]
    description: Text
    category: AgentCategory
    lifecycle: AgentLifecycle
    default_enabled: bool
    capabilities: frozenset[DottedId] = frozenset()
    manifest: AgentManifest
