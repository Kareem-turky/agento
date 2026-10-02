"""Product Skill and Task inspection API (Task 033): READ-ONLY views of the immutable,
Product-owned Skill and Task catalogs. There is no create, update, delete, install or run
endpoint: Skills and Tasks are reviewed Product source code, and this API only describes
them. Nothing here calls a model, a tool, a provider or the network.

    GET /api/v1/skills/catalog                 agents.read
    GET /api/v1/skills/skill?skill_id=         agents.read
    GET /api/v1/tasks/catalog                  agents.read
    GET /api/v1/tasks/task?task_id=            agents.read

Paths are FIXED (ids are query parameters) so the AgentOS authentication exemption stays a
list of exact paths. Product authentication only; never AgentOS.
"""

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from app.agent_management import AgentCategory, AgentLifecycle
from app.agent_management.capabilities import ProductCapabilityGraph
from app.agent_management.skills import SkillDefinition
from app.agent_management.tasks import TaskDefinition, TaskInputKind
from app.context import CurrentActor, CurrentRequestContext
from app.routes.agents import _service, _sync  # shared fixed error mapping
from app.routes.integrations import SafeValidationRoute

SKILLS_CATALOG_PATH = "/api/v1/skills/catalog"
SKILL_PATH = "/api/v1/skills/skill"
TASKS_CATALOG_PATH = "/api/v1/tasks/catalog"
TASK_PATH = "/api/v1/tasks/task"
CAPABILITIES_PATHS = (SKILLS_CATALOG_PATH, SKILL_PATH, TASKS_CATALOG_PATH, TASK_PATH)

router = APIRouter(tags=["skills and tasks"], route_class=SafeValidationRoute)
_FROZEN = ConfigDict(frozen=True, extra="forbid")
_READ_ONLY = (
    "Immutable, Product-owned metadata (read-only): it describes capabilities and never "
    "authorizes anything; Product governance still decides every action."
)

# ----- models -----------------------------------------------------------------------------------


class SkillView(BaseModel):
    model_config = _FROZEN
    skill_id: str
    name: str
    description: str
    category: AgentCategory
    lifecycle: AgentLifecycle
    capabilities: list[str]
    tool_ids: list[str] = Field(
        description="Product tool IDs (their access and governed "
        "actions are in the owning Agent's manifest)."
    )
    requirements: list[str] = Field(description="Product domain requirements (never a provider).")
    agent_ids: list[str] = Field(description="Product Agents that possess this Skill.")
    task_ids: list[str] = Field(description="Product Tasks that require this Skill.")

    @classmethod
    def of(cls, skill: SkillDefinition, graph: ProductCapabilityGraph) -> "SkillView":
        return cls(
            skill_id=skill.skill_id, name=skill.name, description=skill.description,
            category=skill.category, lifecycle=skill.lifecycle,
            capabilities=sorted(skill.capabilities), tool_ids=list(skill.tool_ids),
            requirements=sorted(skill.requirements),
            agent_ids=list(graph.agents_with_skill(skill.skill_id)),
            task_ids=list(graph.tasks_using_skill(skill.skill_id)),
        )  # fmt: skip


class TaskInputFieldView(BaseModel):
    model_config = _FROZEN
    name: str
    label: str
    kind: TaskInputKind
    required: bool
    description: str
    max_length: int | None


class AcceptanceCriterionView(BaseModel):
    """A stable code and its description: Product metadata, never an executable rule."""

    model_config = _FROZEN
    code: str
    description: str


class TaskLimitsView(BaseModel):
    """Declared execution envelope: contract metadata validated against the Agent manifest.
    Not a security boundary and grants nothing (the runtime limits remain authoritative)."""

    model_config = _FROZEN
    max_tool_calls: int
    writes_possible: bool
    allowed_write_actions: list[str]
    requires_explicit_write_intent: bool


class TaskView(BaseModel):
    model_config = _FROZEN
    task_id: str
    name: str
    description: str
    category: AgentCategory
    lifecycle: AgentLifecycle
    skill_ids: list[str]
    inputs: list[TaskInputFieldView]
    acceptance_criteria: list[AcceptanceCriterionView]
    limits: TaskLimitsView
    agent_ids: list[str] = Field(description="Product Agents that support this Task.")

    @classmethod
    def of(cls, task: TaskDefinition, graph: ProductCapabilityGraph) -> "TaskView":
        limits = task.limits
        return cls(
            task_id=task.task_id, name=task.name, description=task.description,
            category=task.category, lifecycle=task.lifecycle, skill_ids=sorted(task.skill_ids),
            inputs=[TaskInputFieldView(**f.model_dump()) for f in task.inputs],
            acceptance_criteria=[AcceptanceCriterionView(**c.model_dump())
                                 for c in task.acceptance_criteria],
            limits=TaskLimitsView(
                max_tool_calls=limits.max_tool_calls, writes_possible=limits.writes_possible,
                allowed_write_actions=sorted(limits.allowed_write_actions),
                requires_explicit_write_intent=limits.requires_explicit_write_intent,
            ),
            agent_ids=list(graph.agents_with_task(task.task_id)),
        )  # fmt: skip


class SkillCatalogResponse(BaseModel):
    model_config = _FROZEN
    request_id: UUID
    skills: list[SkillView]


class SkillResponse(BaseModel):
    model_config = _FROZEN
    request_id: UUID
    skill: SkillView


class TaskCatalogResponse(BaseModel):
    model_config = _FROZEN
    request_id: UUID
    tasks: list[TaskView]


class TaskResponse(BaseModel):
    model_config = _FROZEN
    request_id: UUID
    task: TaskView


# ----- routes -----------------------------------------------------------------------------------

_ID_PATTERN = r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$"
SkillIdQuery = Annotated[str, Query(description="A Product Skill id.", max_length=128,
                                    pattern=_ID_PATTERN)]  # fmt: skip
TaskIdQuery = Annotated[str, Query(description="A Product Task id.", max_length=128,
                                   pattern=_ID_PATTERN)]  # fmt: skip
_ERRORS: dict[int | str, dict[str, Any]] = {
    401: {"description": "Missing or invalid Product API key."},
    403: {"description": "The actor lacks `agents.read`."},
    503: {"description": "Agent management unavailable."},
}
_ONE: dict[int | str, dict[str, Any]] = {
    **_ERRORS,
    404: {"description": "No such Skill/Task in this build."},
    422: {"description": "Invalid id (submitted values are never echoed)."},
}


def _docs(text: str) -> str:
    return f"{text} {_READ_ONLY}\n\nRequires the `agents.read` Product permission."


@router.get(SKILLS_CATALOG_PATH, response_model=SkillCatalogResponse, responses=_ERRORS,
            summary="List Product Skills",
            description=_docs("The Product Skills of this build, their tool bindings and the "
                              "Agents and Tasks that use them."))  # fmt: skip
async def get_skill_catalog(
    context: CurrentRequestContext, actor: CurrentActor, request: Request
) -> SkillCatalogResponse:
    service = _service(request)
    skills = _sync(lambda: service.skill_catalog(context))
    graph = service.capabilities
    return SkillCatalogResponse(request_id=context.request_id,
                                skills=[SkillView.of(s, graph) for s in skills])  # fmt: skip


@router.get(SKILL_PATH, response_model=SkillResponse, responses=_ONE,
            summary="Get one Product Skill", description=_docs("One Product Skill."))  # fmt: skip
async def get_skill(
    skill_id: SkillIdQuery, context: CurrentRequestContext, actor: CurrentActor, request: Request
) -> SkillResponse:
    service = _service(request)
    skill = _sync(lambda: service.get_skill(context, skill_id))
    return SkillResponse(request_id=context.request_id,
                         skill=SkillView.of(skill, service.capabilities))  # fmt: skip


@router.get(TASKS_CATALOG_PATH, response_model=TaskCatalogResponse, responses=_ERRORS,
            summary="List Product Tasks",
            description=_docs("The Product Tasks of this build: required Skills, input-field "
                              "metadata, acceptance criteria and declared limits (contract "
                              "metadata, not a security boundary)."))  # fmt: skip
async def get_task_catalog(
    context: CurrentRequestContext, actor: CurrentActor, request: Request
) -> TaskCatalogResponse:
    service = _service(request)
    tasks = _sync(lambda: service.task_catalog(context))
    graph = service.capabilities
    return TaskCatalogResponse(request_id=context.request_id,
                               tasks=[TaskView.of(t, graph) for t in tasks])  # fmt: skip


@router.get(TASK_PATH, response_model=TaskResponse, responses=_ONE,
            summary="Get one Product Task", description=_docs("One Product Task."))  # fmt: skip
async def get_task(
    task_id: TaskIdQuery, context: CurrentRequestContext, actor: CurrentActor, request: Request
) -> TaskResponse:
    service = _service(request)
    task = _sync(lambda: service.get_task(context, task_id))
    return TaskResponse(request_id=context.request_id,
                        task=TaskView.of(task, service.capabilities))  # fmt: skip
