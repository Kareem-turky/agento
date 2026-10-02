"""Product Skills, Tasks and the cross-catalog capability graph (Task 033)."""

import pytest
from pydantic import ValidationError

from app.agent_management import OPERATIONS_AGENT_DEFINITION, ProductAgentCatalog
from app.agent_management.capabilities import (
    CapabilityGraphError,
    ProductCapabilityGraph,
    build_default_capability_graph,
)
from app.agent_management.skills import (
    DAILY_ANALYSIS_SKILL,
    ORDER_INSPECTION_SKILL,
    TICKET_ESCALATION_SKILL,
    ProductSkillCatalog,
    SkillDefinition,
    build_default_skill_catalog,
)
from app.agent_management.tasks import (
    ANALYZE_DAILY_TASK,
    ESCALATE_ISSUE_TASK,
    INSPECT_ORDER_TASK,
    ProductTaskCatalog,
    TaskAcceptanceCriterion,
    TaskDefinition,
    TaskInputField,
    TaskLimits,
    build_default_task_catalog,
)

SKILLS = {"operations.order_inspection", "operations.daily_analysis",
          "operations.ticket_escalation"}  # fmt: skip
TASKS = {"operations.inspect_order", "operations.analyze_daily", "operations.escalate_issue"}


def graph(agent=OPERATIONS_AGENT_DEFINITION, skills=None, tasks=None) -> ProductCapabilityGraph:
    return ProductCapabilityGraph.build(
        ProductAgentCatalog([agent]),
        skills if skills is not None else build_default_skill_catalog(),
        tasks if tasks is not None else build_default_task_catalog(),
    )


def problems(**kwargs) -> tuple[str, ...]:
    with pytest.raises(CapabilityGraphError) as error:
        graph(**kwargs)
    return error.value.problems


# ----- the real catalogs ----------------------------------------------------------------------


def test_default_catalogs_are_exactly_the_operations_capabilities() -> None:
    g = build_default_capability_graph()
    assert g.agents.agent_ids == {"operations"}
    assert g.skills.skill_ids == SKILLS and g.tasks.task_ids == TASKS
    assert OPERATIONS_AGENT_DEFINITION.skill_ids == SKILLS
    assert OPERATIONS_AGENT_DEFINITION.task_ids == TASKS
    assert ORDER_INSPECTION_SKILL.tool_ids == ("get_order", "get_order_shipments")
    assert DAILY_ANALYSIS_SKILL.tool_ids == ("get_daily_operations_report",)
    assert TICKET_ESCALATION_SKILL.tool_ids == ("create_operational_ticket",)
    assert INSPECT_ORDER_TASK.skill_ids == {"operations.order_inspection"}
    assert ANALYZE_DAILY_TASK.skill_ids == {"operations.daily_analysis"}
    assert ESCALATE_ISSUE_TASK.skill_ids == {"operations.ticket_escalation"}
    # Every manifest tool is covered by exactly one Skill (no duplicate tool system).
    covered = [t for s in g.skills.definitions() for t in s.tool_ids]
    assert sorted(covered) == sorted(t.tool_id for t in OPERATIONS_AGENT_DEFINITION.manifest.tools)


def test_acceptance_criteria_reflect_the_existing_runtime_contract() -> None:
    codes = {t.task_id: {c.code for c in t.acceptance_criteria}
             for t in build_default_task_catalog().definitions()}  # fmt: skip
    assert {"facts_from_tools", "store_scope_preserved", "no_fabricated_results",
            "no_provider_payloads"} <= codes["operations.inspect_order"]  # fmt: skip
    assert {"daily_report_used", "daily_report_authoritative", "no_report_recalculation",
            "no_invented_findings", "business_date_not_guessed", "coverage_respected",
            "no_reconstruction_on_failure"} <= codes["operations.analyze_daily"]  # fmt: skip
    assert {"explicit_write_intent_required", "agent_cannot_authorize_writes",
            "governance_authoritative", "verified_write_only",
            "unconfirmed_is_not_created"} <= codes["operations.escalate_issue"]  # fmt: skip


def test_task_limits_are_within_the_agent_limit_and_writes_are_explicit() -> None:
    limit = OPERATIONS_AGENT_DEFINITION.manifest.tool_call_limit
    for task in build_default_task_catalog().definitions():
        assert task.limits.max_tool_calls <= limit
    assert not INSPECT_ORDER_TASK.limits.writes_possible
    assert not ANALYZE_DAILY_TASK.limits.writes_possible
    assert ESCALATE_ISSUE_TASK.limits.allowed_write_actions == {"operations.ticket.create"}
    assert ESCALATE_ISSUE_TASK.limits.requires_explicit_write_intent


# ----- definitions are immutable, strict and non-executable --------------------------------------


def test_definitions_are_immutable_and_strict() -> None:
    with pytest.raises(ValidationError):
        ORDER_INSPECTION_SKILL.tool_ids = ("x",)  # type: ignore[misc]
    with pytest.raises(ValidationError):
        INSPECT_ORDER_TASK.limits.max_tool_calls = 50  # type: ignore[misc]
    for extra in ({"code": "app.x:Y"}, {"prompt": "do anything"}, {"handler": "eval"}):
        with pytest.raises(ValidationError):
            SkillDefinition.model_validate(ORDER_INSPECTION_SKILL.model_dump() | extra)
        with pytest.raises(ValidationError):
            TaskDefinition.model_validate(INSPECT_ORDER_TASK.model_dump() | extra)
    for bad_id in ("Operations.X", "operations", "app.agents.operations:Agent", "../x", ""):
        with pytest.raises(ValidationError):
            SkillDefinition.model_validate(ORDER_INSPECTION_SKILL.model_dump()
                                           | {"skill_id": bad_id})  # fmt: skip
    # Input kinds are a fixed vocabulary; no schema/validator code can be smuggled in.
    with pytest.raises(ValidationError):
        TaskInputField(name="x", label="X", kind="json_schema", required=True,  # type: ignore[arg-type]
                       description="d")  # fmt: skip
    with pytest.raises(ValidationError):
        TaskInputField(name="x", label="X", kind="uuid", required=True, description="d",  # type: ignore[arg-type]
                       max_length=5)  # fmt: skip
    with pytest.raises(ValidationError):
        TaskAcceptanceCriterion(code="lambda x: x", description="d")
    # A task's envelope is self-consistent and carries no permission.
    assert "permission" not in "".join(TaskLimits.model_fields).lower()
    with pytest.raises(ValidationError):
        TaskLimits(max_tool_calls=1, writes_possible=True, requires_explicit_write_intent=True)
    with pytest.raises(ValidationError):
        TaskLimits(max_tool_calls=1, writes_possible=True, requires_explicit_write_intent=False,
                   allowed_write_actions=frozenset({"operations.ticket.create"}))  # fmt: skip
    with pytest.raises(ValidationError):
        TaskLimits(max_tool_calls=1, writes_possible=False, requires_explicit_write_intent=False,
                   allowed_write_actions=frozenset({"operations.ticket.create"}))  # fmt: skip


def test_catalogs_are_immutable_and_reject_duplicates() -> None:
    skills, tasks = build_default_skill_catalog(), build_default_task_catalog()
    for catalog in (skills, tasks):
        with pytest.raises(AttributeError):
            catalog.extra = 1  # type: ignore[attr-defined]
        assert not [n for n in dir(catalog) if n.startswith(("add", "register", "load", "remove"))]
    with pytest.raises(TypeError):
        skills._skills["x"] = ORDER_INSPECTION_SKILL  # type: ignore[index]
    with pytest.raises(TypeError):
        tasks._tasks["x"] = INSPECT_ORDER_TASK  # type: ignore[index]
    with pytest.raises(ValueError, match="duplicate skill"):
        ProductSkillCatalog([ORDER_INSPECTION_SKILL, ORDER_INSPECTION_SKILL])
    with pytest.raises(ValueError, match="duplicate task"):
        ProductTaskCatalog([INSPECT_ORDER_TASK, INSPECT_ORDER_TASK])
    for foreign in ("operations.order_inspection", {"skill_id": "x.y"}, object()):
        with pytest.raises(TypeError):
            ProductSkillCatalog([foreign])  # type: ignore[list-item]
        with pytest.raises(TypeError):
            ProductTaskCatalog([foreign])  # type: ignore[list-item]


# ----- cross-catalog validation fails closed ------------------------------------------------------


def test_dangling_agent_references_fail() -> None:
    agent = OPERATIONS_AGENT_DEFINITION.model_copy(
        update={
            "skill_ids": OPERATIONS_AGENT_DEFINITION.skill_ids | {"operations.missing_skill"},
            "task_ids": OPERATIONS_AGENT_DEFINITION.task_ids | {"operations.missing_task"},
        }
    )
    found = problems(agent=agent)
    assert "agent operations: unknown skill operations.missing_skill" in found
    assert "agent operations: unknown task operations.missing_task" in found


def test_task_with_unknown_or_uninstalled_skill_fails() -> None:
    orphan = INSPECT_ORDER_TASK.model_copy(update={
        "skill_ids": frozenset({"operations.order_inspection", "operations.ghost"})})  # fmt: skip
    found = problems(tasks=ProductTaskCatalog([orphan, ANALYZE_DAILY_TASK, ESCALATE_ISSUE_TASK]))
    assert "task operations.inspect_order: unknown skill operations.ghost" in found
    agent = OPERATIONS_AGENT_DEFINITION.model_copy(
        update={
            "skill_ids": frozenset({"operations.order_inspection", "operations.daily_analysis"})
        }
    )
    found = problems(agent=agent)
    assert ("task operations.escalate_issue: skill operations.ticket_escalation not installed "
            "on agent operations") in found  # fmt: skip


def test_skill_tool_must_exist_in_the_agent_manifest() -> None:
    rogue = ORDER_INSPECTION_SKILL.model_copy(update={"tool_ids": ("get_order", "delete_order")})
    found = problems(skills=ProductSkillCatalog([rogue, DAILY_ANALYSIS_SKILL,
                                                 TICKET_ESCALATION_SKILL]))  # fmt: skip
    assert ("skill operations.order_inspection: tool delete_order not in agent operations "
            "manifest") in found  # fmt: skip


def test_task_limit_cannot_exceed_the_agent_limit() -> None:
    greedy = INSPECT_ORDER_TASK.model_copy(
        update={
            "limits": TaskLimits(
                max_tool_calls=7, writes_possible=False, requires_explicit_write_intent=False
            )
        }
    )
    found = problems(tasks=ProductTaskCatalog([greedy, ANALYZE_DAILY_TASK, ESCALATE_ISSUE_TASK]))
    assert found == ("task operations.inspect_order: tool-call limit exceeds agent operations "
                     "manifest limit",)  # fmt: skip


def test_write_envelope_must_match_the_manifest() -> None:
    undeclared = ESCALATE_ISSUE_TASK.model_copy(update={"limits": TaskLimits(
        max_tool_calls=2, writes_possible=True, requires_explicit_write_intent=True,
        allowed_write_actions=frozenset({"operations.ticket.create", "operations.refund.create"}),
    )})  # fmt: skip
    found = problems(tasks=ProductTaskCatalog([INSPECT_ORDER_TASK, ANALYZE_DAILY_TASK,
                                               undeclared]))  # fmt: skip
    assert ("task operations.escalate_issue: write action operations.refund.create not "
            "declared by agent operations manifest") in found  # fmt: skip
    sneaky = INSPECT_ORDER_TASK.model_copy(update={"skill_ids": frozenset({
        "operations.order_inspection", "operations.ticket_escalation"})})  # fmt: skip
    found = problems(tasks=ProductTaskCatalog([sneaky, ANALYZE_DAILY_TASK, ESCALATE_ISSUE_TASK]))
    assert ("task operations.inspect_order: write envelope does not match its skills' tools"
            in found)  # fmt: skip


def test_skill_capabilities_and_requirements_must_be_the_agents() -> None:
    inflated = DAILY_ANALYSIS_SKILL.model_copy(update={
        "capabilities": frozenset({"operations.daily_report.explain", "finance.forecast"}),
        "requirements": frozenset({"commerce.payments.read"})})  # fmt: skip
    found = problems(skills=ProductSkillCatalog([ORDER_INSPECTION_SKILL, inflated,
                                                 TICKET_ESCALATION_SKILL]))  # fmt: skip
    assert ("skill operations.daily_analysis: capability finance.forecast not on agent "
            "operations") in found  # fmt: skip
    assert ("skill operations.daily_analysis: requirement commerce.payments.read not on agent "
            "operations") in found  # fmt: skip


def test_graph_relations_and_type_safety() -> None:
    g = build_default_capability_graph()
    assert g.agents_with_skill("operations.daily_analysis") == ("operations",)
    assert g.tasks_using_skill("operations.ticket_escalation") == ("operations.escalate_issue",)
    assert g.agents_with_task("operations.inspect_order") == ("operations",)
    with pytest.raises(TypeError):
        ProductCapabilityGraph.build(g.agents, list(g.skills.definitions()), g.tasks)  # type: ignore[arg-type]
