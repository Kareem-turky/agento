"""The Operations Agent in Employee Chat (Task 042): the REAL Agno tool loop with a scripted
model. The chat Agent is offered the read tools and ``propose_operational_ticket`` only;
the proposal writes nothing (no coordinator run, audit event or ticket), is typed and
validated, at most one per turn, needs the actor's ticket permission, and the bounded
history reaches the model as plain user/assistant context."""

import asyncio
import json
from dataclasses import dataclass
from typing import Any

import pytest

from app.agents.operations import OperationsRunFailedError
from app.agents.operations_chat import (
    CHAT_INSTRUCTIONS,
    CHAT_PROPOSAL_KEY,
    TICKET_PREPARED_MESSAGE,
    OperationsChatRunner,
    build_operations_chat_agent,
)
from app.agents.operations_context import OPERATIONS_CONTEXT_KEY
from app.employee_chat.models import HistoryTurn, ProposedTicket
from app.execution import ActionHandlerRegistry
from app.governance import ActionCatalog, ActionScope, GovernanceGate
from app.integrations.commerce.mock import (
    MockCommerceAdapter,
    MockCommerceSystem,
    MockTicketDesk,
    MockTicketingAdapter,
)
from app.operations import OPERATIONS_ACTIONS, CreateOperationalTicketHandler
from app.workflows import DailyOperationsWorkflow
from tests.agents.helpers import COMPANY, STORE, SpyCoordinator, actor, request
from tests.execution.fakes import FIXED_TIME, RecordingAuditSink
from tests.support.scripted_tool_model import CallTool, Reply, ScriptedToolModel

SCOPE = ActionScope(company_id=COMPANY, store_id=STORE)
TITLE, BODY = "Investigate failed shipment", "Review the failed shipment."


@dataclass
class ChatStack:
    model: ScriptedToolModel
    coordinator: SpyCoordinator
    sink: RecordingAuditSink
    desk: MockTicketDesk


def chat(script: list) -> tuple[ChatStack, OperationsChatRunner]:
    """The real chat Agent on the real gate, coordinator, mock desk and daily workflow."""
    model = ScriptedToolModel(script=list(script))
    commerce = MockCommerceAdapter()
    desk = MockTicketDesk()
    sink = RecordingAuditSink()
    gate = GovernanceGate(ActionCatalog(OPERATIONS_ACTIONS))
    coordinator = SpyCoordinator(
        gate,
        ActionHandlerRegistry(
            [CreateOperationalTicketHandler(MockTicketingAdapter(MockCommerceSystem(), desk))]
        ),
        sink,
        clock=lambda: FIXED_TIME,
    )
    daily = DailyOperationsWorkflow(commerce=commerce, gate=gate, clock=lambda: FIXED_TIME)
    agent = build_operations_chat_agent(
        model, commerce=commerce, gate=gate, coordinator=coordinator, daily_operations=daily
    )
    return ChatStack(model, coordinator, sink, desk), OperationsChatRunner(agent)


def run_chat(
    runner: OperationsChatRunner, message: str, history: tuple = (), req: Any = None
) -> Any:
    return asyncio.run(runner.run_chat(req or request(), SCOPE, message, history))


def offered(stack: Any) -> list[str]:
    tools = stack.model.requests[0].tools or []
    return sorted((t.get("function") or t)["name"] for t in tools)


def nothing_executed(stack: Any) -> None:
    assert stack.coordinator.runs == 0 and stack.sink.events == [] and stack.desk.ticket_count == 0


def test_chat_offers_read_tools_and_the_proposal_only() -> None:
    stack, runner = chat([Reply("hello")])
    result = run_chat(runner, "hi")
    assert result.message == "hello" and result.proposal is None
    assert offered(stack) == [
        "get_daily_operations_report",
        "get_order",
        "get_order_shipments",
        "propose_operational_ticket",
    ]
    nothing_executed(stack)


def test_a_proposal_is_typed_announced_and_executes_nothing() -> None:
    stack, runner = chat(
        [
            CallTool("propose_operational_ticket", {"title": f"  {TITLE} ", "description": BODY}),
            Reply("Ticket created!"),
        ]
    )
    result = run_chat(runner, "create a ticket")
    assert result.proposal == ProposedTicket(title=TITLE, description=BODY)
    (tool_result,) = stack.model.tool_results()["propose_operational_ticket"]
    assert tool_result == {
        "status": "proposed",
        "reason": "awaiting_confirmation",
        "message": TICKET_PREPARED_MESSAGE,
    }
    # The adversarial model claimed "Ticket created!": its free-form final text is
    # discarded and ONLY the Product-owned message is returned.
    assert result.message == TICKET_PREPARED_MESSAGE
    assert "Ticket created" not in result.message
    assert stack.coordinator.runs == 0
    assert stack.sink.events == []
    assert stack.desk.ticket_count == 0


def test_at_most_one_proposal_per_turn() -> None:
    stack, runner = chat(
        [
            CallTool("propose_operational_ticket", {"title": "first", "description": "one"}),
            CallTool("propose_operational_ticket", {"title": "second", "description": "two"}),
            Reply(TICKET_PREPARED_MESSAGE),
        ]
    )
    result = run_chat(runner, "two tickets")
    assert result.proposal == ProposedTicket(title="first", description="one")
    second = stack.model.tool_results()["propose_operational_ticket"][-1]
    assert second["status"] == "rejected" and second["reason"] == "one_proposal_per_turn"
    nothing_executed(stack)


@pytest.mark.parametrize(
    "arguments",
    [
        {"title": "", "description": "d"},
        {"title": "t", "description": ""},
        {"title": "x" * 161, "description": "d"},
        {"title": "t", "description": "y" * 4001},
    ],
)
def test_an_invalid_proposal_is_rejected(arguments: dict[str, str]) -> None:
    stack, runner = chat([CallTool("propose_operational_ticket", arguments), Reply("ok")])
    result = run_chat(runner, "ticket")
    assert result.proposal is None
    (tool_result,) = stack.model.tool_results()["propose_operational_ticket"]
    assert tool_result["status"] == "rejected"


def test_a_proposal_needs_the_actor_ticket_permission() -> None:
    stack, runner = chat(
        [CallTool("propose_operational_ticket", {"title": TITLE, "description": BODY}), Reply("ok")]
    )
    reader = request(actor(permissions=frozenset({"orders.read", "shipments.read"})))
    result = run_chat(runner, "ticket", req=reader)
    assert result.proposal is None
    (tool_result,) = stack.model.tool_results()["propose_operational_ticket"]
    assert tool_result == {"status": "denied", "reason": "permission_denied", "message": None}


def test_the_write_tool_cannot_be_called_from_chat() -> None:
    stack, runner = chat(
        [
            CallTool("create_operational_ticket", {"title": TITLE, "description": BODY}),
            Reply("done"),
        ]
    )
    result = run_chat(runner, "create it now")
    assert result.proposal is None
    nothing_executed(stack)


def test_history_is_plain_context_before_the_current_message() -> None:
    stack, runner = chat([Reply("third answer")])
    history = (
        HistoryTurn(user_text="q1", assistant_text="a1"),
        HistoryTurn(user_text="q2", assistant_text="a2"),
    )
    run_chat(runner, "q3", history)
    seen = [(m.role, m.content) for m in stack.model.requests[0].messages if m.role != "system"]
    assert seen == [
        ("user", "q1"),
        ("assistant", "a1"),
        ("user", "q2"),
        ("assistant", "a2"),
        ("user", "q3"),
    ]
    system = "\n".join(m.content for m in stack.model.requests[0].messages if m.role == "system")
    # The trusted context and the proposal sink are never model context.
    for hidden in (OPERATIONS_CONTEXT_KEY, CHAT_PROPOSAL_KEY, "ops-user-7f3a", "tickets.create"):
        assert hidden not in system


def test_chat_instructions_never_allow_a_write() -> None:
    text = " ".join(CHAT_INSTRUCTIONS)
    assert "Only create a ticket when" not in text
    assert "propose_operational_ticket" in text and TICKET_PREPARED_MESSAGE in text
    assert "context only" in text


def test_a_failed_run_raises_without_detail() -> None:
    class Boom(Exception):
        pass

    stack, runner = chat([])

    async def broken(*args: Any, **kwargs: Any) -> Any:
        raise Boom("provider detail")

    stack.model.ainvoke = broken  # type: ignore[method-assign]
    with pytest.raises((OperationsRunFailedError, Boom)):
        run_chat(runner, "hi")


def test_tool_results_are_json() -> None:
    stack, runner = chat(
        [CallTool("propose_operational_ticket", {"title": TITLE, "description": BODY}), Reply("x")]
    )
    run_chat(runner, "t")
    raw = next(m.content for m in stack.model.requests[-1].messages if m.role == "tool")
    assert json.loads(raw)["status"] == "proposed"
