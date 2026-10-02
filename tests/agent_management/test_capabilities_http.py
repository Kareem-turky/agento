"""Read-only Product Skill / Task inspection API (Task 033): Product authentication,
agents.read, safe unknown/malformed ids, no mutation surface, OpenAPI, and proof that
reading the static catalogs makes no model, tool, provider or network call."""

import socket

import pytest
from agno.os.settings import AgnoAPISettings
from fastapi.testclient import TestClient

from app.main import create_app
from tests.conftest import TEST_OS_SECURITY_KEY
from tests.support.agent_fakes import RecordingOperationsService, build_agent_service
from tests.support.product_auth import deployment_settings, principal

READER = "test-skills-reader-key-" + "r" * 24
NOBODY = "test-skills-nobody-key-" + "n" * 24
SKILLS, SKILL = "/api/v1/skills/catalog", "/api/v1/skills/skill"
TASKS, TASK = "/api/v1/tasks/catalog", "/api/v1/tasks/task"
PATHS = (SKILLS, SKILL, TASKS, TASK)
ONE = {SKILL: {"skill_id": "operations.order_inspection"},
       TASK: {"task_id": "operations.escalate_issue"}}  # fmt: skip


def auth(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> list[object]:
    attempts: list[object] = []

    def refuse(_socket: object, address: object) -> None:
        attempts.append(address)
        raise AssertionError("unexpected outbound connection")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", refuse)
    return attempts


@pytest.fixture
def world(settings):
    service, repository, audit = build_agent_service()
    operations = RecordingOperationsService()
    keys = (principal(READER, key_id="reader", actor_id="reader",
                      permissions=frozenset({"agents.read"})),
            principal(NOBODY, key_id="nobody", actor_id="nobody",
                      permissions=frozenset({"orders.read", "tickets.create"})))  # fmt: skip
    app = create_app(deployment_settings(settings, "test", product_api_keys=keys),
                     AgnoAPISettings(os_security_key=TEST_OS_SECURITY_KEY),
                     operations_service=operations, agent_service=service)  # fmt: skip
    return app, operations, repository, audit


def test_unauthenticated_wrong_key_and_agentos_key_are_refused(world) -> None:
    app, *_ = world
    with TestClient(app) as client:
        for path in PATHS:
            params = ONE.get(path, {})
            assert client.get(path, params=params).status_code == 401
            assert (
                client.get(path, params=params, headers=auth("test-wrong-" + "x" * 40)).status_code
                == 401
            )
            assert client.get(path, params=params,
                              headers=auth(TEST_OS_SECURITY_KEY)).status_code == 401  # fmt: skip


def test_agents_read_is_required(world) -> None:
    app, *_ = world
    with TestClient(app) as client:
        for path in PATHS:
            denied = client.get(path, params=ONE.get(path, {}), headers=auth(NOBODY))
            assert denied.status_code == 403 and denied.json() == {"detail": "Forbidden"}


def test_reader_inspects_agent_skill_task_relationships(world, no_network) -> None:
    app, operations, repository, audit = world
    with TestClient(app) as client:
        skills = client.get(SKILLS, headers=auth(READER)).json()["skills"]
        tasks = client.get(TASKS, headers=auth(READER)).json()["tasks"]
        agent = client.get("/api/v1/agents/agent", params={"agent_id": "operations"},
                           headers=auth(READER)).json()["agent"]["definition"]  # fmt: skip
        skill = client.get(SKILL, params=ONE[SKILL], headers=auth(READER)).json()["skill"]
        task = client.get(TASK, params=ONE[TASK], headers=auth(READER)).json()["task"]
    assert [s["skill_id"] for s in skills] == ["operations.daily_analysis",
                                               "operations.order_inspection",
                                               "operations.ticket_escalation"]  # fmt: skip
    assert [t["task_id"] for t in tasks] == ["operations.analyze_daily",
                                             "operations.escalate_issue",
                                             "operations.inspect_order"]  # fmt: skip
    assert agent["skill_ids"] == [s["skill_id"] for s in skills]
    assert agent["task_ids"] == [t["task_id"] for t in tasks]
    assert skill["tool_ids"] == ["get_order", "get_order_shipments"]
    assert skill["agent_ids"] == ["operations"] and skill["task_ids"] == [
        "operations.inspect_order"
    ]
    assert task["skill_ids"] == ["operations.ticket_escalation"]
    assert task["limits"] == {"max_tool_calls": 2, "writes_possible": True,
                              "allowed_write_actions": ["operations.ticket.create"],
                              "requires_explicit_write_intent": True}  # fmt: skip
    assert "verified_write_only" in {c["code"] for c in task["acceptance_criteria"]}
    assert [f["name"] for f in task["inputs"]] == ["title", "description"]
    # Static metadata: no model, tool, provider, network, storage or audit activity.
    assert operations.calls == [] and no_network == []
    assert repository.rows == {} and audit.events == []


def test_unknown_and_malformed_ids_fail_safely(world) -> None:
    app, *_ = world
    with TestClient(app) as client:
        for path, key in ((SKILL, "skill_id"), (TASK, "task_id")):
            missing = client.get(path, params={key: "operations.unknown"}, headers=auth(READER))
            assert missing.status_code == 404
            assert missing.json()["detail"] in ("Skill not found", "Task not found")
            for bad in ("app.agents.operations:Agent", "../etc", "Operations.X", "x" * 200):
                response = client.get(path, params={key: bad}, headers=auth(READER))
                assert response.status_code == 422 and bad not in response.text
            assert client.get(path, headers=auth(READER)).status_code == 422


def test_no_mutation_surface(world) -> None:
    app, *_ = world
    with TestClient(app) as client:
        for path in PATHS:
            for method in ("POST", "PUT", "PATCH", "DELETE"):
                response = client.request(
                    method,
                    path,
                    params=ONE.get(path, {}),
                    headers=auth(READER),
                    json={"tool_ids": ["x"]},
                )
                assert response.status_code == 405, (method, path)
        for path in ("/api/v1/tasks/task/run", "/api/v1/skills/install", "/api/v1/tasks"):
            assert client.post(path, headers=auth(READER)).status_code in (401, 404, 405)
        after = client.get(SKILL, params=ONE[SKILL], headers=auth(READER)).json()["skill"]
    assert after["tool_ids"] == ["get_order", "get_order_shipments"]


def test_openapi_documents_read_only_skill_and_task_routes(world) -> None:
    app, *_ = world
    with TestClient(app) as client:
        schema = client.get("/openapi.json").json()
    paths = {p: schema["paths"][p] for p in PATHS}
    for path, operations in paths.items():
        assert set(operations) == {"get"}, path
        operation = operations["get"]
        assert operation["tags"] == ["skills and tasks"] and operation["summary"]
        assert "`agents.read`" in operation["description"]
        assert "read-only" in operation["description"]
    components = schema["components"]["schemas"]
    for name in ("SkillCatalogResponse", "TaskCatalogResponse", "SkillView", "TaskView",
                 "TaskLimitsView", "AcceptanceCriterionView", "TaskInputFieldView"):  # fmt: skip
        assert name in components, name
    assert "not a security boundary" in components["TaskLimitsView"]["description"].lower()
