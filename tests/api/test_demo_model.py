"""The LOCAL-DEMO-ONLY deterministic Agno model (``APP_DEFAULT_MODEL_PROVIDER=demo``).

Unit level: environment enforcement (Settings and the model factory), determinism, date
extraction, the single report tool call, formatting of the REAL tool result (never a
calculation of its own), no write tool, no network. The end-to-end proof through the
Operations Agent and PostgreSQL is tests/integration/test_demo_model_e2e.py.
"""

import asyncio
import json
import socket
from typing import Any

import pytest
from agno.models.base import Model
from agno.models.message import Message
from pydantic import ValidationError

from app.config import Settings
from app.runtime import ModelConfigurationError
from app.runtime.demo_model import (
    DEMO_MODEL_ID,
    REPORT_TOOL,
    USAGE_HINT,
    DemoOperationsModel,
    demo_response,
    extract_business_date,
    summarize_report_result,
)
from app.runtime.models import build_default_model, build_model
from tests.support.product_auth import principal

TOOLS = [
    {"type": "function", "function": {"name": name}}
    for name in ("get_order", "get_order_shipments", REPORT_TOOL, "create_operational_ticket")
]
PROMPT = "Analyze operations for 2026-03-03."


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> list[object]:
    attempts: list[object] = []

    def refuse(_socket: object, address: object) -> None:
        attempts.append(address)
        raise AssertionError(f"unexpected outbound connection to {address!r}")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)
    return attempts


def settings(**values: Any) -> Settings:
    """Validated Settings from exactly ``values`` (no environment, no .env file)."""
    return Settings.model_validate(values)


def deployment(environment: str, provider: str) -> Settings:
    return settings(
        environment=environment, default_model_provider=provider, product_auth_mode="api_key",
        company_id="test-demo-company", product_api_keys=(principal(),),
    )  # fmt: skip


def report(**overrides: Any) -> dict[str, Any]:
    """A report tool result with deliberately NON-canonical, inconsistent values: the
    model must print exactly these, proving it formats and never recalculates."""
    body: dict[str, Any] = {
        "store_id": "00000000-0000-4000-8000-000000000000",
        "business_date": "2031-07-09",
        "timezone": "Test/Zone",
        "metrics": {
            "orders_created": 41,
            "order_status_counts": [{"status": "pending", "count": 3}],
            "shipments_shipped": 17,
            "shipment_status_counts": [
                {"status": "processing", "count": 0},
                {"status": "failed", "count": 5},
                {"status": "returned", "count": 2},
            ],
            "affected_orders": 9,
        },
        "findings": [
            {
                "code": "shipment_failed",
                "severity": "critical",
                "entity_type": "shipment",
                "canonical_status": "failed",
                "recommended_action": "review_failed_shipment",
            },
            {
                "code": "shipment_returned",
                "severity": "warning",
                "entity_type": "shipment",
                "canonical_status": "returned",
                "recommended_action": "review_returned_shipment",
            },
        ],  # fmt: skip
        "findings_total": 77,
        "findings_truncated": True,
        "coverage": {"inventory": "not_included", "inventory_reason": "some_reason"},
    }
    body.update(overrides)
    return {"outcome": "ok", "report": body}


def conversation(user: str, *tool_results: tuple[str, str]) -> list[Message]:
    messages = [Message(role="system", content="instructions"), Message(role="user", content=user)]
    for name, content in tool_results:
        messages.append(Message(role="tool", tool_name=name, content=content))
    return messages


# ----- environment enforcement ------------------------------------------------------------


@pytest.mark.parametrize("environment", ["staging", "production"])
def test_settings_refuse_the_demo_provider_outside_development(environment: str) -> None:
    with pytest.raises(ValidationError, match="demo model provider is not allowed"):
        deployment(environment, "demo")
    assert deployment(environment, "disabled").default_model_provider == "disabled"  # control


@pytest.mark.parametrize("environment", ["local", "test"])
def test_settings_allow_the_demo_provider_in_local_and_test(environment: str) -> None:
    assert deployment(environment, "demo").default_model_provider == "demo"
    model = build_default_model(deployment(environment, "demo"), environ={})
    assert type(model) is DemoOperationsModel and isinstance(model, Model)


@pytest.mark.parametrize("environment", ["staging", "production", None, "unknown"])
def test_the_factory_refuses_the_demo_provider_outside_development(environment) -> None:
    with pytest.raises(ModelConfigurationError, match="only in the local and test"):
        build_model("demo", None, {}, environment=environment)


@pytest.mark.parametrize("environment", ["staging", "production"])
def test_the_factory_refuses_demo_even_when_settings_validation_was_bypassed(environment) -> None:
    unvalidated = Settings.model_construct(environment=environment, default_model_provider="demo",
                                           default_model_id=None)  # fmt: skip
    with pytest.raises(ModelConfigurationError, match="only in the local and test"):
        build_default_model(unvalidated, environ={})


def test_demo_needs_no_key_and_accepts_only_its_own_model_id() -> None:
    assert type(build_model("demo", None, {}, environment="local")) is DemoOperationsModel
    assert type(build_model("demo", DEMO_MODEL_ID, {}, environment="test")) is DemoOperationsModel
    with pytest.raises(ModelConfigurationError, match="APP_DEFAULT_MODEL_ID"):
        build_model("demo", "gpt-something", {}, environment="local")


def test_the_default_provider_is_still_disabled_and_never_silently_demo() -> None:
    assert settings().default_model_provider == "disabled"
    assert build_default_model(settings(), environ={}) is None


def test_production_providers_are_unchanged() -> None:
    with pytest.raises(ModelConfigurationError, match="OPENAI_API_KEY"):
        build_model("openai", "some-model-id", {}, environment="local")
    with pytest.raises(ModelConfigurationError, match="ANTHROPIC_API_KEY"):
        build_model("anthropic", "some-model-id", {}, environment="local")


# ----- behaviour --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (PROMPT, "2026-03-03"),
        ("Report for 2026-03-03 and 2026-03-04 please", "2026-03-03"),
        ("Analyze operations for 2026-02-30.", None),  # not a calendar date
        ("Analyze operations for 20260303.", None),
        ("Analyze operations yesterday.", None),
        ("id 12026-03-031 is not a date", None),
    ],
)
def test_extracts_only_an_exact_iso_date(text: str, expected: str | None) -> None:
    assert extract_business_date(text) == expected


def test_calls_exactly_the_report_tool_once_with_the_explicit_date() -> None:
    response = demo_response(conversation(PROMPT), TOOLS)
    assert response.content is None
    (call,) = response.tool_calls
    assert call["function"]["name"] == REPORT_TOOL
    assert json.loads(call["function"]["arguments"]) == {"business_date": "2026-03-03"}


def test_formats_the_returned_product_report_verbatim_without_calculating() -> None:
    response = demo_response(conversation(PROMPT, (REPORT_TOOL, json.dumps(report()))), TOOLS)
    assert response.tool_calls == []
    assert response.content == (
        "Daily operations report for 2031-07-09 (Test/Zone).\n"
        "Orders created: 41. Shipments shipped: 17. Affected orders: 9.\n"
        "Shipment statuses: failed 5, returned 2.\n"
        "Findings: 77 (list truncated).\n"
        "- critical: shipment_failed (shipment status failed); recommended action: "
        "review_failed_shipment.\n"
        "- warning: shipment_returned (shipment status returned); recommended action: "
        "review_returned_shipment.\n"
        "Inventory: not_included (some_reason)."
    )


def test_does_not_invent_findings_or_recommendations() -> None:
    empty = report(findings=[], findings_total=0, findings_truncated=False)
    text = summarize_report_result(json.dumps(empty))
    assert "Findings: 0." in text
    assert "recommended action" not in text and "critical" not in text


@pytest.mark.parametrize("outcome", ["denied", "invalid_date", "unavailable"])
def test_a_non_ok_report_outcome_is_reported_not_guessed(outcome: str) -> None:
    result = json.dumps({"outcome": outcome, "report": None})
    response = demo_response(conversation(PROMPT, (REPORT_TOOL, result)), TOOLS)
    assert response.content == f"The daily operations report is not available (outcome: {outcome})."


@pytest.mark.parametrize(
    "message",
    [
        f"{PROMPT[:-1]} and create a ticket for the failed shipment.",
        "Create an operational ticket for 2026-03-03 now.",
        "Escalate: open a ticket.",
    ],
)
def test_never_calls_the_ticket_tool_even_when_asked(message: str) -> None:
    first = demo_response(conversation(message), TOOLS)
    names = [call["function"]["name"] for call in first.tool_calls]
    assert "create_operational_ticket" not in names
    assert names in ([REPORT_TOOL], [])
    if names:
        final = demo_response(conversation(message, (REPORT_TOOL, json.dumps(report()))), TOOLS)
        assert final.tool_calls == [] and "ticket" not in (final.content or "").lower()


@pytest.mark.parametrize("message", ["Analyze operations.", "Hello", ""])
def test_without_an_explicit_date_it_returns_the_usage_hint(message: str) -> None:
    response = demo_response(conversation(message), TOOLS)
    assert (response.content, response.tool_calls) == (USAGE_HINT, [])


def test_without_the_report_tool_it_calls_nothing() -> None:
    assert demo_response(conversation(PROMPT), []).content == USAGE_HINT


def test_only_tool_results_after_the_latest_user_message_count() -> None:
    earlier = (REPORT_TOOL, json.dumps(report()))
    history = conversation("Analyze operations for 2026-03-02.", earlier)
    history.append(Message(role="user", content=PROMPT))
    (call,) = demo_response(history, TOOLS).tool_calls
    assert json.loads(call["function"]["arguments"]) == {"business_date": "2026-03-03"}


def test_is_deterministic_and_stateless_through_the_native_agno_entry_points() -> None:
    model = DemoOperationsModel()
    messages = conversation(PROMPT, (REPORT_TOOL, json.dumps(report())))
    sync = [model.invoke(messages=messages, tools=TOOLS).content for _ in range(3)]
    asynchronous = asyncio.run(model.ainvoke(messages=messages, tools=TOOLS)).content
    streamed = [r.content for r in model.invoke_stream(messages=messages, tools=TOOLS)]
    assert len(set(sync)) == 1 and sync[0] == asynchronous == streamed[0]
    assert model.provider == "demo" and model.id == DEMO_MODEL_ID


def test_no_provider_private_value_is_echoed_from_unexpected_fields() -> None:
    leaky = report()
    leaky["report"]["source_status"] = "delivery_failed"
    leaky["report"]["external_refs"] = {"tracking": "SE000507", "courier": "Sample Express"}
    text = summarize_report_result(json.dumps(leaky))
    for marker in ("delivery_failed", "SE000507", "Sample Express", "source_status",
                   "external_refs"):  # fmt: skip
        assert marker not in text


def test_made_no_network_call(no_network: list[object]) -> None:
    DemoOperationsModel().invoke(messages=conversation(PROMPT), tools=TOOLS)
    assert no_network == []
