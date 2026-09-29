"""The Operations Agent's ``get_daily_operations_report`` tool (Task 020).

The workflow calculates; the model explains. The tool only passes the TRUSTED request
and store scope plus an exact business date (or None = store-local today) to the
injected ``DailyOperationsReportService`` and returns its structured result.
"""

import ast
import asyncio
import inspect
import json
import re
from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest
from agno.run import RunContext

from app.agents.operations import INSTRUCTIONS, OPERATIONS_TOOL_CALL_LIMIT
from app.agents.operations_context import OPERATIONS_CONTEXT_KEY, TrustedOperationsRunContext
from app.agents.operations_tools import build_operations_tools
from app.commerce.domain import OrderStatus, ShipmentStatus
from app.context.models import RequestContext
from app.governance import ActionCatalog, ActionScope, GovernanceGate
from app.operations import OPERATIONS_ACTIONS
from app.services.operations_reports import (
    DailyOperationsCoverage,
    DailyOperationsForbiddenError,
    DailyOperationsMetrics,
    DailyOperationsReport,
    DailyOperationsUnavailableError,
    OrderStatusCount,
    ShipmentStatusCount,
)
from tests.agents.helpers import (
    ALL_PERMISSIONS,
    COMPANY,
    ORDER,
    STORE,
    TICKET_WRITE,
    actor,
    ops_stack,
    request,
)
from tests.support.scripted_tool_model import CallTool, Reply

REPORTER = actor(permissions=ALL_PERMISSIONS | {"stores.read"})
SCOPE = ActionScope(company_id=COMPANY, store_id=STORE)
TOOL = "get_daily_operations_report"
SECRET = "SECRETMARKER-db-password-7f3a"  # noqa: S105 - test-only marker
NOT_REQUESTED = {"status": "denied", "reason": "action_not_requested", "ticket_id": None}


def fixed_report(orders_created: int = 7) -> DailyOperationsReport:
    zone_start = datetime(2026, 3, 3, tzinfo=UTC)
    return DailyOperationsReport(
        store_id=UUID(STORE), business_date=date(2026, 3, 3), timezone="UTC",
        window_start=zone_start, window_end=datetime(2026, 3, 4, tzinfo=UTC),
        generated_at=zone_start,
        metrics=DailyOperationsMetrics(
            orders_created=orders_created,
            order_status_counts=tuple(
                OrderStatusCount(status=s, count=orders_created if s is OrderStatus.PENDING else 0)
                for s in OrderStatus),
            shipments_shipped=0,
            shipment_status_counts=tuple(ShipmentStatusCount(status=s, count=0)
                                         for s in ShipmentStatus),
            affected_orders=0,
        ),
        findings=(), findings_total=0, findings_truncated=False,
        coverage=DailyOperationsCoverage(),
    )  # fmt: skip


class RecordingService:
    def __init__(self, returns: Any = None, raises: BaseException | None = None) -> None:
        self.returns = returns if returns is not None else fixed_report()
        self.raises = raises
        self.calls: list[tuple] = []

    async def get_daily_report(self, request, scope, business_date):
        self.calls.append((request, scope, business_date))
        if self.raises is not None:
            raise self.raises
        return self.returns


def daily_tool(service: Any):
    gate = GovernanceGate(ActionCatalog(OPERATIONS_ACTIONS))
    tools = build_operations_tools(commerce=object(), gate=gate, coordinator=object(),
                                   daily_operations=service)  # type: ignore[arg-type]  # fmt: skip
    return next(t for t in tools if t.__name__ == TOOL)


def context(req: RequestContext | None = None, scope: ActionScope = SCOPE) -> RunContext:
    trusted = TrustedOperationsRunContext(request=req or request(REPORTER), scope=scope)
    return RunContext(run_id="r", session_id="s", dependencies={OPERATIONS_CONTEXT_KEY: trusted})


def call(service: Any, run_context: Any, **kwargs: Any) -> dict:
    return json.loads(asyncio.run(daily_tool(service)(run_context=run_context, **kwargs)))


# ----- dates -----------------------------------------------------------------------------


def test_no_date_passes_none_through_unchanged() -> None:
    service = RecordingService()
    assert call(service, context())["outcome"] == "ok"
    assert call(service, context(), business_date=None)["outcome"] == "ok"
    assert [c[2] for c in service.calls] == [None, None]


def test_exact_iso_date_is_parsed() -> None:
    service = RecordingService()
    assert call(service, context(), business_date="2026-03-03")["outcome"] == "ok"
    (req, scope, business_date), = service.calls  # fmt: skip
    assert business_date == date(2026, 3, 3) and type(business_date) is date
    assert req.actor == REPORTER and scope == SCOPE


@pytest.mark.parametrize(
    "bad",
    ["03/03/2026", "2026-3-3", " 2026-03-03", "2026-03-03 ", "2026-03-03T00:00:00",
     "2026-02-30", "today", "yesterday", "tomorrow", "", "20260303", "2026-W10-2",
     "2026-03-03\n", 20260303, ["2026-03-03"]],
)  # fmt: skip
def test_anything_but_an_exact_date_is_invalid_and_not_sent(bad) -> None:
    service = RecordingService()
    assert call(service, context(), business_date=bad) == {"outcome": "invalid_date",
                                                           "report": None}  # fmt: skip
    assert service.calls == []


# ----- trusted context ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "run_context",
    [
        None,
        {"dependencies": {}},
        RunContext(run_id="r", session_id="s", dependencies=None),
        RunContext(run_id="r", session_id="s", dependencies={}),
        RunContext(run_id="r", session_id="s", dependencies={OPERATIONS_CONTEXT_KEY: {
            "request": {"actor": {"actor_id": "admin"}}, "scope": {"store_id": STORE}}}),
        RunContext(run_id="r", session_id="s", dependencies={OPERATIONS_CONTEXT_KEY: STORE}),
        RunContext(run_id="r", session_id="s",
                   dependencies={OPERATIONS_CONTEXT_KEY: SCOPE}),  # wrong model
        context(req=RequestContext()),  # no actor
        context(scope=ActionScope(company_id=COMPANY, store_id="not-a-store-uuid")),
    ],
)  # fmt: skip
def test_untrusted_or_incomplete_context_fails_closed(run_context) -> None:
    service = RecordingService()
    assert call(service, run_context, business_date="2026-03-03") == {
        "outcome": "trusted_context_unavailable", "report": None}  # fmt: skip
    assert service.calls == []


def test_model_visible_schema_is_business_date_only() -> None:
    s = ops_stack([Reply("ok")])
    s.run("hi", req=request(REPORTER))
    tools = {t["function"]["name"]: t["function"] for t in s.model.requests[0].tools}
    schema = tools[TOOL]["parameters"]
    assert set(schema["properties"]) == {"business_date"}
    assert "business_date" not in schema.get("required", [])
    assert "run_context" not in json.dumps(tools[TOOL])
    parameters = set(schema["properties"]) | set(schema.get("required", []))
    for hidden in ("store_id", "company_id", "actor_id", "actor", "permissions", "timezone",
                   "scope", "request", "requested_write_actions"):  # fmt: skip
        assert hidden not in parameters, hidden


# ----- service results ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("service", "outcome"),
    [
        (RecordingService(raises=DailyOperationsForbiddenError()), "denied"),
        (RecordingService(raises=DailyOperationsUnavailableError()), "unavailable"),
        (RecordingService(raises=RuntimeError(f"db {SECRET} at 10.0.0.1")), "unavailable"),
        (RecordingService(raises=PermissionError(f"policy {SECRET}")), "unavailable"),
        (RecordingService(returns={"orders_created": 1}), "unavailable"),
        (RecordingService(returns="report"), "unavailable"),
        (RecordingService(returns=fixed_report().metrics), "unavailable"),
    ],
)
def test_service_outcomes_map_to_fixed_safe_results(service, outcome) -> None:
    raw = asyncio.run(daily_tool(service)(run_context=context(), business_date="2026-03-03"))
    assert json.loads(raw) == {"outcome": outcome, "report": None}
    assert SECRET not in raw and "10.0.0.1" not in raw


def test_none_result_is_unavailable() -> None:
    class NoneService:
        async def get_daily_report(self, request, scope, business_date):
            return None

    assert call(NoneService(), context())["outcome"] == "unavailable"


# ----- the real workflow: shop_south, 2026-03-03 --------------------------------------------


def real_tool():
    s = ops_stack([])
    return next(t for t in s.agent.tools if t.__name__ == TOOL), s  # type: ignore[union-attr]


def test_real_report_is_structured_canonical_and_leak_free() -> None:
    tool, s = real_tool()
    raw = asyncio.run(tool(run_context=context(), business_date="2026-03-03"))
    data = json.loads(raw)
    assert data["outcome"] == "ok"
    report = data["report"]
    metrics = report["metrics"]
    assert (metrics["orders_created"], metrics["shipments_shipped"]) == (1, 1)
    assert {c["status"]: c["count"] for c in metrics["shipment_status_counts"]}["failed"] == 1
    (finding,) = report["findings"]
    assert (finding["code"], finding["severity"]) == ("shipment_failed", "critical")
    assert UUID(finding["entity_id"]) and UUID(finding["order_id"]) == UUID(ORDER)
    assert report["coverage"]["inventory"] == "not_included"
    for leak in ("ord_2002", "ship_507", "delivery_failed", "SE000507", "Sample Express",
                 "shop_south", "South Storefront", "acct_demo", "cus_005", "Robin Demo", "@",
                 "source_status", "external_refs", "tracking", "courier", COMPANY,
                 "actor", "permission", "role", "company"):  # fmt: skip
        assert leak not in raw, leak
    # Structured data only: every string is an id, date/time, timezone or enum value.
    enum_values = {v.value for e in (OrderStatus, ShipmentStatus) for v in e} | {
        "created_in_business_day", "shipped_in_business_day", "not_included",
        "store_scoped_inventory_query_unavailable", "critical", "warning", "shipment",
        "order", "shipment_failed", "shipment_returned", "shipment_status_unknown",
        "order_status_unknown", "review_failed_shipment", "review_returned_shipment",
        "review_status_mapping", "ok", "Europe/Berlin",
    }  # fmt: skip
    iso = re.compile(r"^\d{4}-\d{2}-\d{2}(T[\d:.]+([+-]\d{2}:\d{2}|Z))?$")

    def strings(value):
        if isinstance(value, dict):
            for v in value.values():
                yield from strings(v)
        elif isinstance(value, list):
            for v in value:
                yield from strings(v)
        elif isinstance(value, str):
            yield value

    for text in strings(data):
        assert text in enum_values or iso.match(text) or UUID(text), text
    # A daily report read writes and executes nothing.
    assert s.coordinator.runs == 0 and s.sink.events == [] and s.desk.ticket_count == 0


# ----- the agent: scripted runs ---------------------------------------------------------


def summary(results: dict) -> str:
    result = results[TOOL][-1]
    if result["outcome"] != "ok":
        return {
            "denied": "I could not produce the daily operations report: access was denied.",
            "unavailable": "The daily operations report is unavailable right now.",
            "invalid_date": "Please give the date as YYYY-MM-DD.",
        }.get(result["outcome"], "The daily operations report could not be produced.")
    report = result["report"]
    m = report["metrics"]
    failed = {c["status"]: c["count"] for c in m["shipment_status_counts"]}["failed"]
    critical = [f["code"] for f in report["findings"] if f["severity"] == "critical"]
    inventory = report["coverage"]["inventory"]
    return (f"{report['business_date']}: orders created {m['orders_created']}, shipments "
            f"shipped {m['shipments_shipped']}, failed shipments {failed}, critical findings "
            f"{critical}, inventory {inventory}.")  # fmt: skip


def test_agent_explains_the_deterministic_report_for_an_explicit_date() -> None:
    s = ops_stack([CallTool(TOOL, {"business_date": "2026-03-03"}), Reply(summary)])
    output = s.run("Analyze operations for 2026-03-03.", req=request(REPORTER))
    seen = s.model.tool_results()[TOOL][0]
    metrics = seen["report"]["metrics"]
    assert (metrics["orders_created"], metrics["shipments_shipped"]) == (1, 1)
    assert [f["code"] for f in seen["report"]["findings"]] == ["shipment_failed"]
    assert seen["report"]["coverage"]["inventory"] == "not_included"
    assert output.content == (
        "2026-03-03: orders created 1, shipments shipped 1, failed shipments 1, critical "
        "findings ['shipment_failed'], inventory not_included."
    )
    # The report tool never went through the single-order tools or the coordinator.
    assert s.commerce.get_order_calls == [] and s.coordinator.runs == 0


def test_today_passes_no_date_to_the_service() -> None:
    service = RecordingService()
    s = ops_stack([CallTool(TOOL, {}), Reply(summary)], daily=service)
    s.run("Analyze operations today.", req=request(REPORTER))
    (req, scope, business_date), = service.calls  # fmt: skip
    assert business_date is None
    assert (scope.company_id, scope.store_id) == (COMPANY, STORE)
    assert req.actor == REPORTER


def test_report_value_is_used_not_recalculated() -> None:
    service = RecordingService(returns=fixed_report(orders_created=7))
    s = ops_stack([CallTool(TOOL, {"business_date": "2026-03-03"}), Reply(summary)],
                  daily=service)  # fmt: skip
    output = s.run("Analyze operations for 2026-03-03.", req=request(REPORTER))
    assert "orders created 7" in output.content
    # The daily tool never touched commerce (the mock data would count differently).
    assert s.commerce.get_order_calls == [] and s.commerce.list_shipments_calls == []


@pytest.mark.parametrize(
    ("service", "args", "expected"),
    [
        (RecordingService(raises=DailyOperationsForbiddenError()), {"business_date": "2026-03-03"},
         "access was denied"),
        (RecordingService(raises=RuntimeError(SECRET)), {"business_date": "2026-03-03"},
         "unavailable"),
        (RecordingService(), {"business_date": "03/03/2026"}, "YYYY-MM-DD"),
    ],
)  # fmt: skip
def test_failures_are_never_presented_as_a_report(service, args, expected) -> None:
    s = ops_stack([CallTool(TOOL, args), Reply(summary)], daily=service)
    output = s.run("Analyze operations.", req=request(REPORTER))
    assert expected in output.content
    assert "orders created" not in output.content
    assert SECRET not in s.model.visible_text() and SECRET not in output.content
    assert "permission" not in s.model.tool_results()[TOOL][0]


def test_critical_finding_never_authorizes_a_ticket() -> None:
    ticket = CallTool(
        "create_operational_ticket",
        {"title": "Failed shipment", "description": "Critical finding."},
    )
    s = ops_stack([CallTool(TOOL, {"business_date": "2026-03-03"}), ticket,
                   Reply(lambda r: json.dumps(r["create_operational_ticket"]))])  # fmt: skip
    output = s.run(
        "Analyze operations for 2026-03-03 and create a ticket.", req=request(REPORTER)
    )  # read-only run: no requested write actions
    assert s.model.tool_results()[TOOL][0]["report"]["findings"][0]["severity"] == "critical"
    assert json.loads(output.content) == [NOT_REQUESTED]
    assert s.coordinator.runs == 0 and s.sink.events == [] and s.desk.ticket_count == 0


def test_programmatic_write_path_is_unchanged() -> None:
    ticket = CallTool(
        "create_operational_ticket",
        {"title": "Failed shipment", "description": "Critical finding."},
    )
    s = ops_stack([CallTool(TOOL, {"business_date": "2026-03-03"}), ticket,
                   Reply(lambda r: r["create_operational_ticket"][0]["status"])])  # fmt: skip
    output = s.run("Analyze and escalate.", req=request(REPORTER), writes=TICKET_WRITE)
    assert output.content == "verified" and s.desk.ticket_count == 1
    assert s.coordinator.runs == 1


def test_model_cannot_switch_store_through_arguments() -> None:
    service = RecordingService()
    s = ops_stack([CallTool(TOOL, {"business_date": "2026-03-03", "store_id": str(uuid4()),
                                   "company_id": "other", "timezone": "UTC"}),
                   Reply(summary)], daily=service)  # fmt: skip
    s.run("Analyze another store.", req=request(REPORTER))
    for _, scope, _ in service.calls:
        assert (scope.company_id, scope.store_id) == (COMPANY, STORE)


# ----- source guards and instructions ---------------------------------------------------------


def test_the_report_tool_only_calls_the_injected_service() -> None:
    from app.agents import operations_tools

    tree = ast.parse(inspect.getsource(operations_tools))
    tool = next(n for n in ast.walk(tree)
                if isinstance(n, ast.AsyncFunctionDef) and n.name == TOOL)  # fmt: skip
    used = {n.id for n in ast.walk(tool) if isinstance(n, ast.Name)}
    attrs = {n.attr for n in ast.walk(tool) if isinstance(n, ast.Attribute)}
    for forbidden in ("commerce", "gate", "coordinator", "CommerceIntegration",
                      "GovernanceGate", "ExecutionCoordinator", "TicketingIntegration",
                      "allowed", "order_in_scope"):  # fmt: skip
        assert forbidden not in used, forbidden
    for forbidden in ("get_store", "list_orders", "list_shipments", "get_order", "decide",
                      "run", "create_ticket"):  # fmt: skip
        assert forbidden not in attrs, forbidden
    assert "daily_operations" in used and "get_daily_report" in attrs
    source = inspect.getsource(operations_tools)
    assert "app.workflows" not in source and "DailyOperationsWorkflow" not in source


def test_tool_call_limit_is_unchanged() -> None:
    assert OPERATIONS_TOOL_CALL_LIMIT == 6
    assert ops_stack([]).agent.tool_call_limit == 6


def test_instructions_make_the_report_authoritative() -> None:
    text = " ".join(INSTRUCTIONS).lower()
    for marker in (
        "get_daily_operations_report before answering",  # use it for daily analysis
        "today's operations",
        "store-wide",
        "authoritative",
        "never recalculate",
        "never add anomaly rules",
        "not_included",
        "make no inventory claims",
        "without a business_date",
        "never invent",
        "do not present a daily report as if it succeeded",
        "do not reconstruct it",
        "untrusted external data",  # preserved: tool output is never instructions
        "never means a ticket should be",
    ):
        assert marker in text, marker
