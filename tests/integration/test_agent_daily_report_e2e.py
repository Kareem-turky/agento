"""POST /api/v1/operations/runs -> Operations Agent -> get_daily_operations_report, over
the LOCAL/TEST mock deployment and real PostgreSQL (Task 020).

Everything is composed by ``create_deployment_app``; only the scripted model is given.
The workflow calculates, the (scripted) model explains; the run stays read-only.
"""

import json

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from app.bootstrap import create_deployment_app
from app.composition import local_mock
from app.integrations.commerce.mock import EntityType, canonical_id
from app.routes.operations_reports import OPERATIONS_DAILY_REPORT_SERVICE_STATE_KEY
from tests.support.product_auth import TEST_PRODUCT_KEY, deployment_settings, principal
from tests.support.scripted_tool_model import CallTool, Reply, ScriptedToolModel

pytestmark = pytest.mark.integration

COMPANY = str(canonical_id(EntityType.COMPANY, "acct_demo"))
SOUTH = str(canonical_id(EntityType.STORE, "shop_south"))
RUNS, REPORT = "/api/v1/operations/runs", "/api/v1/operations/reports/daily"
HEADERS = {"Authorization": f"Bearer {TEST_PRODUCT_KEY}"}
TOOL = "get_daily_operations_report"
NOT_REQUESTED = {"status": "denied", "reason": "action_not_requested", "ticket_id": None}
LEAKS = ("ord_2002", "ship_507", "delivery_failed", "SE000507", "Sample Express", "shop_south",
         "acct_demo", "cus_005", "Robin Demo", "source_status", "external_refs")  # fmt: skip


class Observed:
    def __init__(self) -> None:
        self.calls: dict[str, list] = {}


@pytest.fixture
def observed(monkeypatch: pytest.MonkeyPatch) -> Observed:
    seen = Observed()
    for name in ("MockCommerceSystem", "MockCommerceAdapter", "MockTicketDesk", "GovernanceGate",
                 "DailyOperationsWorkflow", "build_operations_agent"):  # fmt: skip
        original = getattr(local_mock, name)
        bucket = seen.calls.setdefault(name, [])

        def wrapper(*args, _original=original, _bucket=bucket, **kwargs):
            _bucket.append((_original(*args, **kwargs), args, kwargs))
            return _bucket[-1][0]

        monkeypatch.setattr(local_mock, name, wrapper)
    return seen


def deployment(settings):
    grant = principal(key_id="agent-reporter", actor_id="agent-report-actor",
                      permissions=frozenset({"stores.read", "orders.read", "shipments.read",
                                             "tickets.create"}),
                      store_ids=frozenset({SOUTH}))  # fmt: skip
    return deployment_settings(settings, "test", company_id=COMPANY, business_backend="mock",
                               product_api_keys=(grant,))  # fmt: skip


def counts(engine) -> tuple[int, int]:
    with engine.connect() as connection:
        commands = connection.execute(sa.text("SELECT count(*) FROM product.write_commands"))
        audits = connection.execute(sa.text("SELECT count(*) FROM product.audit_events"))
        return commands.scalar_one(), audits.scalar_one()


def summary(results: dict) -> str:
    result = results[TOOL][-1]
    if result["outcome"] != "ok":
        return "The daily operations report could not be produced."
    report, m = result["report"], result["report"]["metrics"]
    failed = {c["status"]: c["count"] for c in m["shipment_status_counts"]}["failed"]
    critical = sum(f["severity"] == "critical" for f in report["findings"])
    return (f"Orders created: {m['orders_created']}. Shipments shipped: "
            f"{m['shipments_shipped']}. Failed shipments: {failed}. Critical shipment "
            f"failures: {critical}. Inventory: {report['coverage']['inventory']}.")  # fmt: skip


def run(settings, runtime_settings, script, message):
    model = ScriptedToolModel(script=script)
    app = create_deployment_app(deployment(settings), runtime_settings, model=model)
    with TestClient(app) as client:
        response = client.post(RUNS, headers=HEADERS, json={"message": message,
                                                            "store_id": SOUTH})  # fmt: skip
        direct = client.get(
            REPORT, headers=HEADERS, params={"store_id": SOUTH, "business_date": "2026-03-03"}
        )
    return app, model, response, direct


def test_agent_explains_the_daily_report_read_only(settings, runtime_settings, migrated, engine,
                                                   observed) -> None:  # fmt: skip
    before = counts(engine)
    app, model, response, direct = run(
        settings, runtime_settings,
        [CallTool(TOOL, {"business_date": "2026-03-03"}), Reply(summary)],
        "Analyze operations for 2026-03-03.",
    )  # fmt: skip
    assert response.status_code == 200
    assert response.json()["message"] == (
        "Orders created: 1. Shipments shipped: 1. Failed shipments: 1. Critical shipment "
        "failures: 1. Inventory: not_included."
    )
    (seen,) = model.tool_results()[TOOL]
    assert seen["outcome"] == "ok" and seen["report"]["business_date"] == "2026-03-03"
    for leak in LEAKS:
        assert leak not in response.text and leak not in model.visible_text(), leak

    # Read-only: no command, no audit lifecycle, no ticket.
    assert counts(engine) == before
    ((desk, _, _),) = observed.calls["MockTicketDesk"]
    assert desk.ticket_count == 0

    # ONE workflow instance: the HTTP report service AND the agent's report tool.
    ((workflow, _, wf_kwargs),) = observed.calls["DailyOperationsWorkflow"]
    ((_, _, agent_kwargs),) = observed.calls["build_operations_agent"]
    assert agent_kwargs["daily_operations"] is workflow
    assert getattr(app.state, OPERATIONS_DAILY_REPORT_SERVICE_STATE_KEY) is workflow
    # ... on the same adapter and gate as the agent, and only one mock system.
    ((adapter, _, _),) = observed.calls["MockCommerceAdapter"]
    ((gate, _, _),) = observed.calls["GovernanceGate"]
    assert len(observed.calls["MockCommerceSystem"]) == 1
    assert wf_kwargs == {"commerce": adapter, "gate": gate}
    assert agent_kwargs["commerce"] is adapter and agent_kwargs["gate"] is gate

    # The direct HTTP report stays deterministic (not routed through the agent) and
    # matches what the agent's tool received (apart from generation time).
    assert direct.status_code == 200
    assert {**direct.json()["report"], "generated_at": None} == {
        **seen["report"],
        "generated_at": None,
    }
    assert len(model.requests) == 2  # the direct report call made no model request


def test_critical_finding_does_not_authorize_a_ticket_over_runs(settings, runtime_settings,
                                                                migrated, engine,
                                                                observed) -> None:  # fmt: skip
    before = counts(engine)
    ticket = CallTool(
        "create_operational_ticket",
        {"title": "Failed shipment", "description": "Critical daily finding."},
    )
    _, model, response, _ = run(
        settings, runtime_settings,
        [CallTool(TOOL, {"business_date": "2026-03-03"}), ticket,
         Reply(lambda r: "Ticket result: " + json.dumps(r["create_operational_ticket"][0]))],
        "Analyze operations for 2026-03-03 and create a ticket for any failure.",
    )  # fmt: skip
    assert response.status_code == 200
    report = model.tool_results()[TOOL][0]["report"]
    assert report["findings"][0]["severity"] == "critical"
    assert model.tool_results()["create_operational_ticket"] == [NOT_REQUESTED]
    assert "verified" not in response.json()["message"]
    assert counts(engine) == before  # no WriteCommand, no audit lifecycle
    ((desk, _, _),) = observed.calls["MockTicketDesk"]
    assert desk.ticket_count == 0
