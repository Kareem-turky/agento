"""LOCAL-DEMO-ONLY deterministic Agno model (``APP_DEFAULT_MODEL_PROVIDER=demo``).

It exists only so a person can exercise the REAL Operations Agent without configuring an
external model provider. It is not an AI provider: no network, no SDK, no randomness, and
it is refused outside ``local``/``test`` (``app.config.Settings`` and
``app.runtime.models.build_default_model``).

It implements the native Agno ``Model`` contract and decides ONLY from the messages of the
current request (stateless, so concurrent runs cannot interfere):

    user message with an exact ISO date (YYYY-MM-DD) and the report tool offered
        -> ONE native tool call: get_daily_operations_report(business_date=<that date>)
    report tool result present after the user message
        -> a fixed-format summary of THAT result (the Product workflow calculated every
           figure, finding and recommended action; nothing is computed or invented here)
    Employee Chat only (the chat-only proposal tool is offered): a user message
    ``... titled "<title>" with description "<description>"``
        -> ONE native tool call: propose_operational_ticket(title, description)
    proposal tool result present after the user message
        -> "Ticket prepared. Confirm the action to create it." (or the refusal)
    anything else
        -> a fixed usage hint

It never calls any other tool, in particular never ``create_operational_ticket``: the
Operations analysis surface stays read-only whatever the user asks, and in Employee Chat
a ticket is only PROPOSED (a human confirms it through the Product API).
"""

import json
import re
from dataclasses import dataclass
from datetime import date
from typing import Any

from agno.models.base import Model
from agno.models.response import ModelResponse

DEMO_MODEL_PROVIDER = "demo"
DEMO_MODEL_ID = "deterministic-operations-demo"
REPORT_TOOL = "get_daily_operations_report"
PROPOSE_TOOL = "propose_operational_ticket"
TICKET_PREPARED = "Ticket prepared. Confirm the action to create it."
_TICKET_REQUEST = re.compile(r'titled "([^"]{1,160})" with description "([^"]{1,4000})"')
_ISO_DATE = re.compile(r"(?<![0-9])([0-9]{4}-[0-9]{2}-[0-9]{2})(?![0-9])")

USAGE_HINT = (
    "Demo model: ask for an operations analysis with an exact business date, for "
    "example: Analyze operations for 2026-03-03."
)


def extract_business_date(text: str) -> str | None:
    """The first exact, valid ``YYYY-MM-DD`` calendar date in ``text`` (as written)."""
    for candidate in _ISO_DATE.findall(text):
        try:
            if date.fromisoformat(candidate).isoformat() == candidate:
                return candidate
        except ValueError:
            continue
    return None


def _field(message: Any, name: str) -> Any:
    return message.get(name) if isinstance(message, dict) else getattr(message, name, None)


def _text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    return json.dumps(content, sort_keys=True, default=str)


def _tool_names(tools: Any) -> set[str]:
    names: set[str] = set()
    for tool in tools or ():
        function = _field(tool, "function")
        name = _field(function, "name") if function is not None else _field(tool, "name")
        if isinstance(name, str):
            names.add(name)
    return names


def summarize_report_result(content: str) -> str:
    """Format the report tool's JSON result. Only values present in it are used."""
    try:
        result = json.loads(content)
    except ValueError:
        return "The daily operations report could not be read."
    if not isinstance(result, dict) or result.get("outcome") != "ok":
        outcome = result.get("outcome") if isinstance(result, dict) else None
        return f"The daily operations report is not available (outcome: {outcome or 'unknown'})."
    report = result["report"]
    metrics, coverage = report["metrics"], report["coverage"]
    shipments = ", ".join(
        f"{count['status']} {count['count']}"
        for count in metrics["shipment_status_counts"]
        if count["count"]
    )
    lines = [
        f"Daily operations report for {report['business_date']} ({report['timezone']}).",
        f"Orders created: {metrics['orders_created']}. "
        f"Shipments shipped: {metrics['shipments_shipped']}. "
        f"Affected orders: {metrics['affected_orders']}.",
        f"Shipment statuses: {shipments or 'none'}.",
        f"Findings: {report['findings_total']}"
        + (" (list truncated)." if report["findings_truncated"] else "."),
    ]
    lines += [
        f"- {finding['severity']}: {finding['code']} ({finding['entity_type']} status "
        f"{finding['canonical_status']}); recommended action: {finding['recommended_action']}."
        for finding in report["findings"]
    ]
    inventory = f"Inventory: {coverage['inventory']}"
    if coverage.get("inventory_reason"):
        inventory += f" ({coverage['inventory_reason']})"
    lines.append(inventory + ".")
    return "\n".join(lines)


def summarize_proposal_result(content: str) -> str:
    try:
        result = json.loads(content)
    except ValueError:
        result = None
    if isinstance(result, dict) and result.get("status") == "proposed":
        return TICKET_PREPARED
    reason = result.get("reason") if isinstance(result, dict) else None
    return f"No ticket was prepared (reason: {reason or 'unknown'})."


def demo_response(messages: Any, tools: Any) -> ModelResponse:
    """The deterministic answer for one model request (a pure function of its input)."""
    messages = list(messages or ())
    last_user = max(
        (index for index, m in enumerate(messages) if _field(m, "role") == "user"), default=None
    )
    if last_user is None:
        return ModelResponse(role="assistant", content=USAGE_HINT)
    for message in reversed(messages[last_user + 1 :]):
        if _field(message, "role") == "tool" and _field(message, "tool_name") == REPORT_TOOL:
            return ModelResponse(
                role="assistant", content=summarize_report_result(_text(_field(message, "content")))
            )
    for message in reversed(messages[last_user + 1 :]):
        if _field(message, "role") == "tool" and _field(message, "tool_name") == PROPOSE_TOOL:
            return ModelResponse(
                role="assistant",
                content=summarize_proposal_result(_text(_field(message, "content"))),
            )
    user_text = _text(_field(messages[last_user], "content"))
    offered = _tool_names(tools)
    ticket = _TICKET_REQUEST.search(user_text)
    if ticket is not None and PROPOSE_TOOL in offered:
        title, description = ticket.groups()
        return ModelResponse(role="assistant", tool_calls=[{
            "id": "demo_call_propose_ticket",
            "type": "function",
            "function": {
                "name": PROPOSE_TOOL,
                "arguments": json.dumps({"title": title, "description": description}),
            },
        }])  # fmt: skip
    business_date = extract_business_date(user_text)
    if business_date is None or REPORT_TOOL not in offered:
        return ModelResponse(role="assistant", content=USAGE_HINT)
    call = {
        "id": "demo_call_daily_report",
        "type": "function",
        "function": {
            "name": REPORT_TOOL,
            "arguments": json.dumps({"business_date": business_date}),
        },
    }
    return ModelResponse(role="assistant", tool_calls=[call])


@dataclass
class DemoOperationsModel(Model):
    """LOCAL-DEMO-ONLY native Agno model. See the module docstring."""

    id: str = DEMO_MODEL_ID
    name: str | None = "DemoOperationsModel"
    provider: str | None = DEMO_MODEL_PROVIDER

    def _respond(self, kwargs: dict[str, Any]) -> ModelResponse:
        return demo_response(kwargs.get("messages"), kwargs.get("tools"))

    def invoke(self, *args: Any, **kwargs: Any) -> ModelResponse:
        return self._respond(kwargs)

    async def ainvoke(self, *args: Any, **kwargs: Any) -> ModelResponse:
        return self._respond(kwargs)

    def invoke_stream(self, *args: Any, **kwargs: Any):
        yield self._respond(kwargs)

    async def ainvoke_stream(self, *args: Any, **kwargs: Any):
        yield self._respond(kwargs)

    def _parse_provider_response(self, response: Any, **kwargs: Any) -> ModelResponse:
        return response

    def _parse_provider_response_delta(self, response: Any) -> ModelResponse:
        return response
