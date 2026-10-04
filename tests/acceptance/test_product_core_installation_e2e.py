"""PRODUCT CORE ACCEPTANCE (Task 040), journeys A, B (Workflow), I and the Agent lifecycle.

ONE real Product installation (``create_deployment_app``, ``test`` environment, ``mock``
business backend, migrated PostgreSQL, Product API-key auth, the deployment observability
runtime): the API starts, the legacy / live / ready health answers hold, the Product schema
is at the required migration head, the Product and AgentOS credential domains stay
separate, System Status needs ``system.read`` and is safe, the existing Product definitions
are served (no TEST-ONLY definition leaks),
the Operations analysis runs the Product Workflow ``operations.daily_report`` (inspectable
through the Workflow API), and a disabled Operations Agent refuses BEFORE any model or
tool call, durably across a restart on the same database.

TEST-ONLY: the deterministic mock fixture, the ``ScriptedToolModel`` and the generic
integration catalog of ``tests/support/product_core.py``. No provider, no network.
"""

from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from alembic.config import Config
from alembic.script import ScriptDirectory
from fastapi.testclient import TestClient

from app.system_operations import EXPECTED_PRODUCT_SCHEMA_REVISION
from tests.conftest import TEST_OS_SECURITY_KEY
from tests.support.canonical_mock import BUSINESS_DATE, COMPANY, NORTH, SOUTH
from tests.support.product_core import (
    DECIDER,
    INTEGRATION_SECRET,
    NO_CREDENTIALS,
    OPERATOR,
    OPERATOR_ID,
    OPERATOR_KEY,
    RESTRICTED,
    CoreInstallation,
    bearer,
    company_state,
    rows,
    wipe_agent_configuration,
)
from tests.support.scripted_tool_model import CallTool, Reply, ScriptedToolModel

pytestmark = pytest.mark.integration

REPORT_TOOL, TICKET_TOOL = "get_daily_operations_report", "create_operational_ticket"
RUNS, REPORT = "/api/v1/operations/runs", "/api/v1/operations/reports/daily"
OPS = {"agent_id": "operations"}
DAILY = "operations.daily_report"
ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def clean_agents(engine: sa.Engine):
    wipe_agent_configuration(engine)
    yield
    wipe_agent_configuration(engine)


def analysis_model() -> ScriptedToolModel:
    return ScriptedToolModel(script=[CallTool(REPORT_TOOL, {"business_date": BUSINESS_DATE}),
                                     Reply("Daily operations analyzed.")])  # fmt: skip


def analyze(client: TestClient, headers: Any = OPERATOR, store: str = SOUTH):
    return client.post(RUNS, headers=headers, json={
        "message": f"Analyze operations for {BUSINESS_DATE}.", "store_id": store})  # fmt: skip


def daily_run_ids(engine: sa.Engine) -> set:
    return {r["run_id"] for r in rows(
        engine, "SELECT run_id FROM product.workflow_runs WHERE company_id = :c AND "
        "workflow_id = :w", c=COMPANY, w=DAILY)}  # fmt: skip


# ----- journey A + I: installation, credentials, System Status ------------------------------


def test_installation_health_credentials_and_system_status(core: CoreInstallation,
                                                           engine: sa.Engine) -> None:  # fmt: skip
    agentos = bearer(TEST_OS_SECURITY_KEY)
    with TestClient(core.app()) as client:
        legacy = client.get("/health")
        assert legacy.status_code == 200 and legacy.json()["status"] == "ok"
        live = client.get("/health/live")
        assert (live.status_code, live.json()) == (200, {"status": "alive"})
        ready = client.get("/health/ready")
        assert (ready.status_code, ready.json()) == (200, {"status": "ready"})

        # Product credentials and AgentOS credentials are separate domains.
        product_route = "/api/v1/agents/catalog"
        assert client.get(product_route, headers=OPERATOR).status_code == 200
        assert client.get(product_route, headers=agentos).status_code == 401
        assert client.get(product_route).status_code == 401
        assert client.get("/agents", headers=bearer(OPERATOR_KEY)).status_code == 401
        assert client.get("/agents", headers=agentos).status_code == 200

        status = client.get("/api/v1/system/status", headers=OPERATOR)
        forbidden = client.get("/api/v1/system/status", headers=DECIDER)  # no system.read
        restricted = client.get("/api/v1/system/status", headers=RESTRICTED)
        anonymous = client.get("/api/v1/system/status", headers=NO_CREDENTIALS)
        via_agentos = client.get("/api/v1/system/status", headers=agentos)

    assert (anonymous.status_code, via_agentos.status_code) == (401, 401)
    assert forbidden.status_code == 403 and restricted.status_code == 403
    assert status.status_code == 200
    body = status.json()
    assert (body["overall"], body["reasons"]) == ("ready", [])
    assert body["components"] == {"application": "ready", "database": "ready",
                                  "product_schema": "ready", "agent_runtime": "ready"}  # fmt: skip
    assert body["application"]["environment"] == "test"
    assert body["observability"] == {"export_mode": "disabled"}
    # The schema the readiness answer vouched for is the Product's required revision,
    # which is also the single migration head (it advances only with reviewed migrations).
    (head,) = ScriptDirectory.from_config(Config(str(ROOT / "alembic.ini"))).get_heads()
    assert head == EXPECTED_PRODUCT_SCHEMA_REVISION
    assert rows(engine, "SELECT version_num FROM product.alembic_version") == [
        {"version_num": head}]  # fmt: skip
    # Safe: no database address, credential, identity, company, path or endpoint.
    database = sa.engine.make_url(str(core.settings.database_url))
    for leaked in (str(database.host), str(database.database), str(database.username),
                   OPERATOR_KEY, TEST_OS_SECURITY_KEY, INTEGRATION_SECRET, COMPANY,
                   OPERATOR_ID, str(core.secrets_dir), "postgresql", "http://",
                   "https://", "/home", "/var/", "/tmp"):  # noqa: S108  # fmt: skip
        assert leaked not in status.text, leaked
    for response in (forbidden, anonymous):
        assert set(response.json()) == {"detail"}


def test_existing_product_definitions_are_served_and_no_test_definition_leaks(
    core: CoreInstallation,
) -> None:
    with TestClient(core.app()) as client:
        agents = client.get("/api/v1/agents/catalog", headers=OPERATOR).json()["agents"]
        skills = client.get("/api/v1/skills/catalog", headers=OPERATOR).json()["skills"]
        tasks = client.get("/api/v1/tasks/catalog", headers=OPERATOR).json()["tasks"]
        workflows = client.get("/api/v1/workflows/catalog", headers=OPERATOR).json()["workflows"]
        integrations = client.get("/api/v1/integrations/catalog", headers=OPERATOR).json()
    # The existing Product definitions are served (reviewed tasks may add more; the exact
    # release catalogs are historical: docs/PRODUCT_CORE_RELEASE_BASELINE.md).
    assert "operations" in {a["agent_id"] for a in agents}
    assert {"operations.daily_analysis", "operations.order_inspection",
            "operations.ticket_escalation"} <= {s["skill_id"] for s in skills}  # fmt: skip
    assert {"operations.analyze_daily", "operations.escalate_issue",
            "operations.inspect_order"} <= {t["task_id"] for t in tasks}  # fmt: skip
    served = {w["workflow_id"] for w in workflows}
    assert DAILY in served and not [w for w in served if w.startswith("testing.")]
    # The installed catalog of THIS acceptance installation is exactly the injected
    # TEST-ONLY one (generic definitions only): the injection seam replaces, never merges.
    assert {i["integration_id"] for i in integrations["integrations"]} == {
        "example-chat", "example-commerce", "example-messaging"}  # fmt: skip


# ----- journey B: the Operations read path runs the Product Workflow ------------------------


def test_operations_analysis_runs_the_daily_workflow_and_stays_read_only(
    core: CoreInstallation, engine: sa.Engine, clean_agents, no_outbound_network
) -> None:
    model = analysis_model()
    before_runs = daily_run_ids(engine)
    with TestClient(core.app(model)) as client:
        before = company_state(engine)
        direct = client.get(REPORT, headers=OPERATOR,
                            params={"store_id": SOUTH, "business_date": BUSINESS_DATE})  # fmt: skip
        ran = analyze(client)
        after = company_state(engine)
        new_runs = daily_run_ids(engine) - before_runs
        listed = client.get("/api/v1/workflows/runs", headers=OPERATOR).json()["runs"]
        details = [client.get("/api/v1/workflows/run", headers=OPERATOR,
                              params={"run_id": str(r)}).json() for r in new_runs]  # fmt: skip
        (desk,) = core.desks

    assert direct.status_code == 200 and ran.status_code == 200
    assert ran.json()["message"] == "Daily operations analyzed."
    (tool_result,) = model.tool_results()[REPORT_TOOL]
    assert tool_result["outcome"] == "ok"
    assert {**tool_result["report"], "generated_at": None} == {
        **direct.json()["report"], "generated_at": None}  # fmt: skip
    # Both the direct report and the Agent's tool ran the durable Product Workflow.
    assert len(new_runs) == 2
    assert {str(r) for r in new_runs} <= {r["run_id"] for r in listed}
    for detail in details:
        assert (detail["run"]["workflow_id"], detail["run"]["status"]) == (DAILY, "succeeded")
        assert [a["step_id"] for a in detail["attempts"]] == ["compute_daily_report"]
        assert detail["events"][-1]["event_type"] == "workflow_succeeded"
    # Read stays read: only Workflow history was added. No command, audit, approval,
    # ticket, message, Knowledge or configuration change.
    assert {k: v for k, v in after.items() if k != "workflow_runs"} == {
        k: v for k, v in before.items() if k != "workflow_runs"}  # fmt: skip
    assert after["workflow_runs"] == before["workflow_runs"] + 2
    assert desk.ticket_count == 0
    assert no_outbound_network == []


def test_the_model_cannot_escalate_an_analysis_into_a_write(
    core: CoreInstallation, engine: sa.Engine, clean_agents, no_outbound_network
) -> None:
    """The model is untrusted. It first tries the ticket tool with arguments that try to
    choose its own trusted context (another store, another actor): the tool contract
    rejects them. It then tries a well-formed write: the Product refuses it on this read
    surface (the run was not an explicit write request)."""
    escalate = CallTool(TICKET_TOOL, {"title": "Escalate", "description": "Model-chosen write",
                                      "store_id": NORTH, "actor_id": "system"})  # fmt: skip
    plain = CallTool(TICKET_TOOL, {"title": "Escalate", "description": "Model-chosen write"})
    model = ScriptedToolModel(script=[CallTool(REPORT_TOOL, {"business_date": BUSINESS_DATE}),
                                      escalate, plain, Reply("Done.")])  # fmt: skip
    with TestClient(core.app(model)) as client:
        before = company_state(engine)
        ran = analyze(client)
        after = company_state(engine)
        (desk,) = core.desks
    assert ran.status_code == 200
    rejected, refused = model.tool_results()[TICKET_TOOL]
    # The tool contract has no store / actor parameter: the call never reached a handler.
    assert "unexpected_keyword_argument" in rejected["raw"] and "ticket_id" not in rejected
    assert refused == {"status": "denied", "reason": "action_not_requested", "ticket_id": None}
    assert {k: v for k, v in after.items() if k != "workflow_runs"} == {
        k: v for k, v in before.items() if k != "workflow_runs"}  # fmt: skip
    assert desk.ticket_count == 0
    assert no_outbound_network == []


# ----- Agent management: disable / enable, durable across a restart ---------------------------


def test_agent_disable_blocks_the_model_enable_restores_and_both_are_durable(
    core: CoreInstallation, engine: sa.Engine, clean_agents, no_outbound_network
) -> None:
    first = analysis_model()
    with TestClient(core.app(first)) as client:
        state = client.get("/api/v1/agents/agent", headers=OPERATOR, params=OPS).json()
        assert state["agent"]["state"]["enabled"] is True
        disabled = client.post("/api/v1/agents/agent/disable", headers=OPERATOR, params=OPS)
        assert disabled.status_code == 200
        assert disabled.json()["agent"]["state"]["enabled"] is False
        refused = analyze(client)
    assert (refused.status_code, refused.json()) == (409, {
        "detail": "Operations Agent is disabled"})  # fmt: skip
    assert first.requests == [] and first.tool_results() == {}  # refused BEFORE the model

    # A brand-new application on the SAME database: still disabled, still no model call.
    second = analysis_model()
    with TestClient(core.app(second)) as client:
        after_restart = client.get("/api/v1/agents/agent", headers=OPERATOR, params=OPS).json()
        still_refused = analyze(client)
        enabled = client.post("/api/v1/agents/agent/enable", headers=OPERATOR, params=OPS)
        works = analyze(client)
    assert after_restart["agent"]["state"]["enabled"] is False
    assert after_restart["agent"]["state"]["source"] != "default"
    assert still_refused.status_code == 409
    assert enabled.status_code == 200 and works.status_code == 200
    assert works.json()["message"] == "Daily operations analyzed."
    assert len(second.requests) == 2  # model -> report tool -> model

    # And the re-enabled state is durable too.
    third = analysis_model()
    with TestClient(core.app(third)) as client:
        final = client.get("/api/v1/agents/agent", headers=OPERATOR, params=OPS).json()
        assert analyze(client).status_code == 200
    assert final["agent"]["state"]["enabled"] is True
    assert rows(
        engine,
        "SELECT agent_id, enabled FROM product.agent_configurations WHERE company_id = :c",
        c=COMPANY,
    ) == [{"agent_id": "operations", "enabled": True}]
    # Agent management is audited by the existing trail (disable, enable), as the operator.
    verified = [r["action_name"] for r in rows(
        engine, "SELECT action_name FROM product.audit_events WHERE company_id = :c AND "
        "actor_id = :a AND event_type = 'verified' ORDER BY recorded_at, occurred_at",
        c=COMPANY, a=OPERATOR_ID)]  # fmt: skip
    assert verified[-2:] == ["agents.agent.disable", "agents.agent.enable"]
    assert no_outbound_network == []
