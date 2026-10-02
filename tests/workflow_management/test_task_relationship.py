"""Task -> Workflow relationship (Task 034): only ``operations.analyze_daily`` is performed by
a Product Workflow (``operations.daily_report``); dangling references fail closed, and the
Task 033 catalogs are otherwise unchanged."""

import pytest
from agno.os.settings import AgnoAPISettings
from fastapi.testclient import TestClient

from app.agent_management import build_default_agent_catalog
from app.agent_management.capabilities import (
    CapabilityGraphError,
    ProductCapabilityGraph,
    build_default_capability_graph,
)
from app.agent_management.skills import build_default_skill_catalog
from app.agent_management.tasks import (
    ANALYZE_DAILY_TASK,
    ProductTaskCatalog,
    build_default_task_catalog,
)
from app.main import create_app
from app.workflow_management import ProductWorkflowCatalog, build_default_workflow_catalog
from tests.conftest import TEST_OS_SECURITY_KEY
from tests.support.agent_fakes import build_agent_service
from tests.support.product_auth import deployment_settings, principal

READER = "test-task-workflow-reader-" + "r" * 24


def test_only_the_daily_analysis_task_is_workflow_backed() -> None:
    graph = build_default_capability_graph()
    assert {t.task_id: t.workflow_id for t in graph.tasks.definitions()} == {
        "operations.analyze_daily": "operations.daily_report",
        "operations.escalate_issue": None,
        "operations.inspect_order": None,
    }
    assert graph.workflows.workflow_ids == {"operations.daily_report"}
    # Task 033 catalogs are unchanged.
    assert graph.agents.agent_ids == {"operations"}
    assert graph.skills.skill_ids == {"operations.order_inspection",
                                      "operations.daily_analysis",
                                      "operations.ticket_escalation"}  # fmt: skip
    assert graph.tasks.task_ids == {"operations.inspect_order", "operations.analyze_daily",
                                    "operations.escalate_issue"}  # fmt: skip


def test_a_dangling_workflow_reference_fails_closed() -> None:
    dangling = ANALYZE_DAILY_TASK.model_copy(update={"workflow_id": "operations.missing_flow"})
    others = [t for t in build_default_task_catalog().definitions()
              if t.task_id != dangling.task_id]  # fmt: skip
    with pytest.raises(CapabilityGraphError) as info:
        ProductCapabilityGraph.build(build_default_agent_catalog(), build_default_skill_catalog(),
                                     ProductTaskCatalog([*others, dangling]))  # fmt: skip
    assert "unknown workflow operations.missing_flow" in str(info.value)
    with pytest.raises(CapabilityGraphError):  # an empty Workflow catalog leaves it dangling
        ProductCapabilityGraph.build(
            build_default_agent_catalog(),
            build_default_skill_catalog(),
            build_default_task_catalog(),
            ProductWorkflowCatalog([]),
        )
    with pytest.raises(ValueError):  # an invalid id never even becomes a Task
        ANALYZE_DAILY_TASK.model_validate(ANALYZE_DAILY_TASK.model_dump()
                                          | {"workflow_id": "app.workflows:Daily"})  # fmt: skip
    with pytest.raises(TypeError):
        ProductCapabilityGraph.build(
            build_default_agent_catalog(),
            build_default_skill_catalog(),
            build_default_task_catalog(),
            {"x": 1},
        )  # type: ignore[arg-type]
    assert ProductCapabilityGraph.build(
        build_default_agent_catalog(),
        build_default_skill_catalog(),
        build_default_task_catalog(),
        build_default_workflow_catalog(),
    ).workflows.workflow_ids == {"operations.daily_report"}


def test_task_inspection_shows_the_workflow_relationship(settings) -> None:
    service, _, _ = build_agent_service()
    keys = (principal(READER, key_id="reader", actor_id="reader",
                      permissions=frozenset({"agents.read"})),)  # fmt: skip
    app = create_app(deployment_settings(settings, "test", product_api_keys=keys),
                     AgnoAPISettings(os_security_key=TEST_OS_SECURITY_KEY),
                     agent_service=service)  # fmt: skip
    with TestClient(app) as client:
        headers = {"Authorization": f"Bearer {READER}"}
        tasks = client.get("/api/v1/tasks/catalog", headers=headers).json()["tasks"]
    assert {t["task_id"]: t["workflow_id"] for t in tasks} == {
        "operations.analyze_daily": "operations.daily_report",
        "operations.escalate_issue": None,
        "operations.inspect_order": None,
    }
