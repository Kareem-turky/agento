"""The Product capability graph: Agents -> Skills -> Tools, Agents -> Tasks -> Skills.

``ProductCapabilityGraph.build`` validates the three immutable catalogs against each other
and FAILS CLOSED (``CapabilityGraphError`` listing every problem) instead of dropping an
invalid reference:

* every Agent Skill and Task exists;
* every Task's required Skills exist and are installed on each Agent supporting the Task;
* every Skill tool ID is declared by each installing Agent's manifest;
* a Skill's capabilities and requirements are declared by each installing Agent;
* a Task's write envelope matches its Skills' tools: allowed write actions are governed
  actions of WRITE tools of those Skills, declared by the manifest; a read-only Task uses
  no write tool;
* a Task's tool-call limit never exceeds an owning Agent's manifest limit;
* a Task's Workflow reference (Task 034) names a Workflow of the Product Workflow catalog.

The AgentManifest stays the single source of truth for tool access and actions.
"""

from dataclasses import dataclass

from app.agent_management.catalog import ProductAgentCatalog, build_default_agent_catalog
from app.agent_management.definitions import AgentDefinition, AgentToolAccess
from app.agent_management.skills import (
    ProductSkillCatalog,
    SkillDefinition,
    build_default_skill_catalog,
)
from app.agent_management.tasks import (
    ProductTaskCatalog,
    TaskDefinition,
    build_default_task_catalog,
)
from app.workflow_management.catalog import ProductWorkflowCatalog, build_default_workflow_catalog


class CapabilityGraphError(ValueError):
    """The catalogs are inconsistent. ``problems`` are stable, value-free descriptions."""

    def __init__(self, problems: tuple[str, ...]) -> None:
        super().__init__("invalid Product capability graph: " + "; ".join(problems))
        self.problems = problems


def _agent_problems(
    agent: AgentDefinition, skills: ProductSkillCatalog, tasks: ProductTaskCatalog
) -> list[str]:
    problems: list[str] = []
    a = agent.agent_id
    tools = {t.tool_id: t for t in agent.manifest.tools}
    installed: dict[str, SkillDefinition] = {}
    for skill_id in sorted(agent.skill_ids):
        skill = skills.get(skill_id)
        if skill is None:
            problems.append(f"agent {a}: unknown skill {skill_id}")
            continue
        installed[skill_id] = skill
        for tool_id in skill.tool_ids:
            if tool_id not in tools:
                problems.append(f"skill {skill_id}: tool {tool_id} not in agent {a} manifest")
        for capability in sorted(skill.capabilities - agent.capabilities):
            problems.append(f"skill {skill_id}: capability {capability} not on agent {a}")
        for requirement in sorted(skill.requirements - agent.manifest.requirements):
            problems.append(f"skill {skill_id}: requirement {requirement} not on agent {a}")
    for task_id in sorted(agent.task_ids):
        task = tasks.get(task_id)
        if task is None:
            problems.append(f"agent {a}: unknown task {task_id}")
            continue
        problems += _task_problems(agent, task, skills, installed, tools)
    return problems


def _task_problems(agent, task: TaskDefinition, skills, installed, tools) -> list[str]:
    problems: list[str] = []
    t, a = task.task_id, agent.agent_id
    write_actions: set[str] = set()
    uses_write_tool = False
    for skill_id in sorted(task.skill_ids):
        if skill_id not in skills:
            problems.append(f"task {t}: unknown skill {skill_id}")
            continue
        if skill_id not in installed:
            problems.append(f"task {t}: skill {skill_id} not installed on agent {a}")
            continue
        for tool_id in installed[skill_id].tool_ids:
            tool = tools.get(tool_id)
            if tool is not None and tool.access is AgentToolAccess.WRITE:
                uses_write_tool = True
                write_actions.update(tool.action_names)
    limits = task.limits
    if limits.max_tool_calls > agent.manifest.tool_call_limit:
        problems.append(f"task {t}: tool-call limit exceeds agent {a} manifest limit")
    if limits.writes_possible != uses_write_tool:
        problems.append(f"task {t}: write envelope does not match its skills' tools")
    for action in sorted(limits.allowed_write_actions - write_actions):
        problems.append(f"task {t}: write action {action} not declared by agent {a} manifest")
    return problems


def _workflow_problems(tasks: ProductTaskCatalog, workflows: ProductWorkflowCatalog) -> list[str]:
    return [f"task {task.task_id}: unknown workflow {task.workflow_id}"
            for task in tasks.definitions()
            if task.workflow_id is not None and task.workflow_id not in workflows]  # fmt: skip


@dataclass(frozen=True, slots=True)
class ProductCapabilityGraph:
    agents: ProductAgentCatalog
    skills: ProductSkillCatalog
    tasks: ProductTaskCatalog
    workflows: ProductWorkflowCatalog

    @classmethod
    def build(
        cls,
        agents: ProductAgentCatalog,
        skills: ProductSkillCatalog,
        tasks: ProductTaskCatalog,
        workflows: ProductWorkflowCatalog | None = None,
    ) -> "ProductCapabilityGraph":
        """``workflows`` defaults to the static Product Workflow catalog of this build."""
        if workflows is None:
            workflows = build_default_workflow_catalog()
        if (
            not isinstance(agents, ProductAgentCatalog)
            or not isinstance(skills, ProductSkillCatalog)
            or not isinstance(tasks, ProductTaskCatalog)
            or not isinstance(workflows, ProductWorkflowCatalog)
        ):
            raise TypeError("the capability graph is built from Product catalogs only")
        problems: list[str] = []
        for agent in agents.definitions():
            problems += _agent_problems(agent, skills, tasks)
        problems += _workflow_problems(tasks, workflows)
        if problems:
            raise CapabilityGraphError(tuple(problems))
        return cls(agents, skills, tasks, workflows)

    def agents_with_skill(self, skill_id: str) -> tuple[str, ...]:
        return tuple(a.agent_id for a in self.agents.definitions() if skill_id in a.skill_ids)

    def agents_with_task(self, task_id: str) -> tuple[str, ...]:
        return tuple(a.agent_id for a in self.agents.definitions() if task_id in a.task_ids)

    def tasks_using_skill(self, skill_id: str) -> tuple[str, ...]:
        return tuple(t.task_id for t in self.tasks.definitions() if skill_id in t.skill_ids)


def build_default_capability_graph() -> ProductCapabilityGraph:
    """The validated Product capability graph of this build (fails closed if inconsistent)."""
    return ProductCapabilityGraph.build(
        build_default_agent_catalog(), build_default_skill_catalog(), build_default_task_catalog()
    )
