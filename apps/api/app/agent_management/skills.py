"""Product Skills (Task 033): WHAT capability an Agent possesses.

A ``SkillDefinition`` is immutable Product metadata describing one coherent capability.
It binds to Product TOOL IDs that the owning Agent's ``AgentManifest`` declares (the
manifest stays the single source of truth for each tool's access and governed actions).
A Skill is not code, an Agno tool, a prompt, an MCP server, a provider adapter or a
workflow, and it never authorizes anything: every tool call is still decided by trusted
actor -> GovernanceGate -> policy -> ExecutionCoordinator -> verification -> audit.

``ProductSkillCatalog`` is the immutable, explicit list of Skills in this build: no
discovery, import paths, database or remote code, and no HTTP registration.
"""

from collections.abc import Iterable
from types import MappingProxyType
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from app.agent_management.definitions import (
    AgentCategory,
    AgentLifecycle,
    DottedId,
    ToolId,
)

_FROZEN = ConfigDict(frozen=True, extra="forbid")
Name = Annotated[str, StringConstraints(strict=True, min_length=1, max_length=80)]
Text = Annotated[str, StringConstraints(strict=True, min_length=1, max_length=500)]


class SkillDefinition(BaseModel):
    model_config = _FROZEN

    skill_id: DottedId
    name: Name
    description: Text
    category: AgentCategory
    lifecycle: AgentLifecycle = AgentLifecycle.ACTIVE
    # Agent capability identifiers this Skill realizes (subset of the Agent's capabilities).
    capabilities: frozenset[DottedId] = Field(min_length=1)
    # Product tool IDs declared by the owning Agent's manifest; never code.
    tool_ids: tuple[ToolId, ...] = Field(min_length=1)
    # Product domain requirements (never a provider); subset of the Agent's requirements.
    requirements: frozenset[DottedId] = frozenset()

    @model_validator(mode="after")
    def _unique_tools(self) -> "SkillDefinition":
        if len(self.tool_ids) != len(set(self.tool_ids)):
            raise ValueError("duplicate tool id")
        return self


class ProductSkillCatalog:
    """Immutable after construction; rejects duplicates and anything but definitions."""

    __slots__ = ("_skills",)

    def __init__(self, skills: Iterable[SkillDefinition]) -> None:
        by_id: dict[str, SkillDefinition] = {}
        for skill in skills:
            if not isinstance(skill, SkillDefinition):
                raise TypeError("the catalog accepts SkillDefinition values only")
            if skill.skill_id in by_id:
                raise ValueError("duplicate skill id")
            by_id[skill.skill_id] = skill
        ordered = sorted(by_id.values(), key=lambda s: s.skill_id)
        object.__setattr__(self, "_skills", MappingProxyType({s.skill_id: s for s in ordered}))

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError("ProductSkillCatalog is immutable")

    def get(self, skill_id: str) -> SkillDefinition | None:
        return self._skills.get(skill_id)

    def definitions(self) -> tuple[SkillDefinition, ...]:
        return tuple(self._skills.values())

    @property
    def skill_ids(self) -> frozenset[str]:
        return frozenset(self._skills)

    def __contains__(self, skill_id: object) -> bool:
        return skill_id in self._skills

    def __len__(self) -> int:
        return len(self._skills)


# ----- the real Skills of this build: exactly what the Operations Agent implements today ------

ORDER_INSPECTION_SKILL = SkillDefinition(
    skill_id="operations.order_inspection",
    name="Order inspection",
    description="Inspect one canonical order of the trusted store and its shipments.",
    category=AgentCategory.OPERATIONS,
    capabilities=frozenset({"operations.analysis"}),
    tool_ids=("get_order", "get_order_shipments"),
    requirements=frozenset({"commerce.orders.read", "commerce.shipments.read"}),
)
DAILY_ANALYSIS_SKILL = SkillDefinition(
    skill_id="operations.daily_analysis",
    name="Daily operations analysis",
    description="Read the deterministic daily operations report and explain and prioritize "
    "its results without recalculating them.",
    category=AgentCategory.OPERATIONS,
    capabilities=frozenset({"operations.daily_report.explain"}),
    tool_ids=("get_daily_operations_report",),
    requirements=frozenset({"commerce.stores.read", "commerce.orders.read",
                            "commerce.shipments.read"}),
)  # fmt: skip
TICKET_ESCALATION_SKILL = SkillDefinition(
    skill_id="operations.ticket_escalation",
    name="Ticket escalation",
    description="Request a governed operational ticket when the trusted run explicitly "
    "requested that write.",
    category=AgentCategory.OPERATIONS,
    capabilities=frozenset({"operations.ticket.request"}),
    tool_ids=("create_operational_ticket",),
    requirements=frozenset({"operations.ticketing.write"}),
)

DEFAULT_SKILLS: tuple[SkillDefinition, ...] = (
    ORDER_INSPECTION_SKILL, DAILY_ANALYSIS_SKILL, TICKET_ESCALATION_SKILL,
)  # fmt: skip


def build_default_skill_catalog() -> ProductSkillCatalog:
    return ProductSkillCatalog(DEFAULT_SKILLS)
