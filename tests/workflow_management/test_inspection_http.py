"""Read-only Workflow inspection API (Task 034): Product authentication, ``workflows.read``,
company-isolated run history with no existence oracle, safe responses, no run/mutation
surface, OpenAPI, and proof that inspection makes no model, tool or network call."""

import asyncio
import json
import socket
from uuid import uuid4

import pytest
from agno.os.settings import AgnoAPISettings
from fastapi.testclient import TestClient

from app.governance import ActionCatalog, GovernanceGate
from app.main import create_app
from app.workflow_management.actions import WORKFLOW_ACTIONS
from app.workflow_management.service import WorkflowInspectionService
from tests.conftest import TEST_OS_SECURITY_KEY
from tests.support.agent_fakes import RecordingOperationsService
from tests.support.product_auth import TEST_COMPANY_ID, deployment_settings, principal
from tests.support.workflow_fakes import (
    TEST_CATALOG,
    InMemoryWorkflowRunRepository,
    TestInput,
    engine,
    registry,
    request,
    scope,
    three_step_handlers,
)

READER = "test-workflow-reader-key-" + "r" * 24
NOBODY = "test-workflow-nobody-key-" + "n" * 24
CATALOG, WORKFLOW = "/api/v1/workflows/catalog", "/api/v1/workflows/workflow"
RUNS, RUN = "/api/v1/workflows/runs", "/api/v1/workflows/run"


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
    repository = InMemoryWorkflowRunRepository()
    platform = engine(repository, registry(three=three_step_handlers(transform=["error"])))

    def execute(company: str, value: int):
        return asyncio.run(platform.execute("testing.three_steps", request(company),
                                            scope(company), TestInput(value=value)))  # fmt: skip

    mine = [execute(TEST_COMPANY_ID, 1234567), execute(TEST_COMPANY_ID, 7654321)]
    foreign = execute("another-company", 5555555)
    gate = GovernanceGate(ActionCatalog(WORKFLOW_ACTIONS))
    service = WorkflowInspectionService(TEST_CATALOG, repository, gate)
    operations = RecordingOperationsService()
    keys = (principal(READER, key_id="reader", actor_id="reader",
                      permissions=frozenset({"workflows.read"})),
            principal(NOBODY, key_id="nobody", actor_id="nobody",
                      permissions=frozenset({"agents.read", "orders.read"})))  # fmt: skip
    app = create_app(deployment_settings(settings, "test", product_api_keys=keys),
                     AgnoAPISettings(os_security_key=TEST_OS_SECURITY_KEY),
                     operations_service=operations, workflow_service=service)  # fmt: skip
    return app, mine, foreign, operations


def ones(mine) -> dict[str, dict[str, str]]:
    return {WORKFLOW: {"workflow_id": "testing.three_steps"}, RUN: {"run_id": str(mine[0].run_id)}}


def test_unauthenticated_wrong_key_and_agentos_key_are_refused(world) -> None:
    app, mine, *_ = world
    with TestClient(app) as client:
        for path in (CATALOG, WORKFLOW, RUNS, RUN):
            params = ones(mine).get(path, {})
            assert client.get(path, params=params).status_code == 401
            wrong = auth("test-wrong-" + "x" * 40)
            assert client.get(path, params=params, headers=wrong).status_code == 401
            agentos = auth(TEST_OS_SECURITY_KEY)
            assert client.get(path, params=params, headers=agentos).status_code == 401


def test_workflows_read_is_required(world) -> None:
    app, mine, *_ = world
    with TestClient(app) as client:
        for path in (CATALOG, WORKFLOW, RUNS, RUN):
            denied = client.get(path, params=ones(mine).get(path, {}), headers=auth(NOBODY))
            assert (denied.status_code, denied.json()) == (403, {"detail": "Forbidden"})


def test_reader_inspects_the_catalog(world) -> None:
    app, *_ = world
    with TestClient(app) as client:
        body = client.get(CATALOG, headers=auth(READER)).json()
        assert [w["workflow_id"] for w in body["workflows"]] == ["testing.governed_write",
                                                                 "testing.three_steps"]  # fmt: skip
        one = client.get(WORKFLOW, params={"workflow_id": "testing.three_steps"},
                         headers=auth(READER)).json()["workflow"]  # fmt: skip
        assert [s["step_id"] for s in one["steps"]] == ["fetch", "transform", "finish"]
        assert one["steps"][0] == {
            "step_id": "fetch", "name": "Fetch", "description": "Test step fetch.",
            "handler_id": "testing.fetch", "side_effect": "read_only", "timeout_seconds": 1,
            "max_attempts": 3, "checkpoint_policy": "state",
        }  # fmt: skip
        missing = client.get(WORKFLOW, params={"workflow_id": "unknown.flow"},
                             headers=auth(READER))  # fmt: skip
        assert (missing.status_code, missing.json()) == (404, {"detail": "Workflow not found"})


def test_run_history_is_company_scoped_and_safe(world) -> None:
    app, mine, foreign, _ = world
    with TestClient(app) as client:
        runs = client.get(RUNS, headers=auth(READER)).json()["runs"]
        assert {r["run_id"] for r in runs} == {str(r.run_id) for r in mine}  # never foreign
        assert all(r["status"] == "failed" and r["failure_code"] == "retry_exhausted"
                   and r["attempt_count"] == 3 for r in runs)  # fmt: skip
        assert len(client.get(RUNS, params={"limit": 1}, headers=auth(READER)).json()["runs"]) == 1
        detail = client.get(RUN, params={"run_id": str(mine[0].run_id)}, headers=auth(READER))
        body = detail.json()
        assert detail.status_code == 200 and body["run"]["current_step_id"] == "transform"
        assert [(a["step_id"], a["attempt"], a["status"]) for a in body["attempts"]] == [
            ("fetch", 1, "succeeded"),
            ("transform", 1, "failed"),
            ("transform", 2, "failed"),
        ]
        assert [e["sequence"] for e in body["events"]] == list(range(1, len(body["events"]) + 1))
        assert body["events"][-1]["event_type"] == "workflow_failed"
        text = json.dumps(body)
        # Never the input, a checkpoint, an output, actor/store ids, the claim or raw errors.
        for leak in ("1234567", "checkpoint\":", "input_state", "lease", "actor_id", "store",
                     "SENSITIVE", "fetched", "user-1"):  # fmt: skip
            assert leak not in text, leak
        # Another company's run is indistinguishable from a missing one.
        for run_id in (foreign.run_id, uuid4()):
            other = client.get(RUN, params={"run_id": str(run_id)}, headers=auth(READER))
            assert (other.status_code, other.json()) == (404, {"detail": "Workflow run not found"})


def test_malformed_ids_and_limits_are_safe_422s(world) -> None:
    app, *_ = world
    with TestClient(app) as client:
        for path, params in ((RUN, {"run_id": "not-a-uuid-SENSITIVE"}), (RUN, {}),
                             (WORKFLOW, {"workflow_id": "Bad Id SENSITIVE"}),
                             (RUNS, {"limit": 0}), (RUNS, {"limit": 101})):  # fmt: skip
            answer = client.get(path, params=params, headers=auth(READER))
            assert answer.status_code == 422 and "SENSITIVE" not in answer.text


def test_there_is_no_run_resume_or_mutation_surface(world) -> None:
    app, mine, *_ = world
    with TestClient(app) as client:
        for path in (CATALOG, WORKFLOW, RUNS, RUN):
            for method in ("POST", "PUT", "PATCH", "DELETE"):
                answer = client.request(method, path, headers=auth(READER))
                assert answer.status_code == 405, (method, path)
        for path in ("/api/v1/workflows/run/resume", "/api/v1/workflows/execute",
                     "/api/v1/workflows/runs/retry", "/api/v1/workflows"):  # fmt: skip
            assert client.post(path, headers=auth(READER)).status_code in (401, 404, 405)


def test_openapi_documents_read_only_workflow_routes(world) -> None:
    app, *_ = world
    with TestClient(app) as client:
        paths = client.get("/openapi.json").json()["paths"]
    workflow_paths = {p: set(v) for p, v in paths.items() if p.startswith("/api/v1/workflows")}
    assert workflow_paths == {CATALOG: {"get"}, WORKFLOW: {"get"}, RUNS: {"get"}, RUN: {"get"}}
    for path in workflow_paths:
        operation = paths[path]["get"]
        assert "workflows.read" in operation["description"]
        assert "Read-only" in operation["description"]
        assert operation["tags"] == ["workflows"]


def test_inspection_calls_no_model_tool_or_network(world, no_network) -> None:
    app, mine, _, operations = world
    with TestClient(app) as client:
        for path in (CATALOG, WORKFLOW, RUNS, RUN):
            client.get(path, params=ones(mine).get(path, {}), headers=auth(READER))
    assert operations.calls == [] and no_network == []


def test_without_a_service_the_routes_answer_503(settings) -> None:
    keys = (principal(READER, key_id="reader", actor_id="reader",
                      permissions=frozenset({"workflows.read"})),)  # fmt: skip
    app = create_app(deployment_settings(settings, "test", product_api_keys=keys),
                     AgnoAPISettings(os_security_key=TEST_OS_SECURITY_KEY))  # fmt: skip
    with TestClient(app) as client:
        answer = client.get(CATALOG, headers=auth(READER))
        assert (answer.status_code, answer.json()) == (503, {"detail": "Workflows unavailable"})


def test_a_failing_store_answers_503_without_details(world) -> None:
    app, mine, *_ = world
    service = app.state.workflow_inspection_service
    service._repository.fail_on = {"list_runs", "get_run"}
    with TestClient(app) as client:
        for path in (RUNS, RUN):
            answer = client.get(path, params=ones(mine).get(path, {}), headers=auth(READER))
            assert (answer.status_code, answer.json()) == (503, {"detail": "Workflows unavailable"})
