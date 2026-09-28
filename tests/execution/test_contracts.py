"""Models, registry and handler contract."""

import pytest
from pydantic import ValidationError

from app.execution import (
    ActionHandler,
    ActionHandlerRegistry,
    ActionRun,
    ActionRunReason,
    ActionRunStatus,
    AuditEvent,
    ExecutionResult,
    VerificationResult,
)
from tests.execution.fakes import FakeHandler


def test_terminal_statuses() -> None:
    assert {s.value for s in ActionRunStatus} == {
        "denied", "awaiting_approval", "failed", "requires_human", "verified",
    }  # fmt: skip


def test_reasons_are_typed_codes() -> None:
    assert ActionRunReason.HANDLER_NOT_REGISTERED.value == "handler_not_registered"
    assert ActionRunReason.INPUT_INVALID.value == "input_invalid"


@pytest.mark.parametrize("model", [ActionRun, ExecutionResult, VerificationResult, AuditEvent])
def test_models_are_frozen_and_reject_unknown_fields(model) -> None:
    assert model.model_config["frozen"] is True
    assert model.model_config["extra"] == "forbid"


def test_execution_result_holds_only_a_safe_reference() -> None:
    assert set(ExecutionResult.model_fields) == {"reference_id"}
    assert ExecutionResult(reference_id="order:123-A").reference_id == "order:123-A"
    for bad in ("", " x", "has space", "a" * 200, "{json}", "<html>"):
        with pytest.raises(ValidationError):
            ExecutionResult(reference_id=bad)
    with pytest.raises(ValidationError):
        ExecutionResult(reference_id="x", raw={"status": 200})
    result = ExecutionResult(reference_id="x")
    with pytest.raises(ValidationError):
        result.reference_id = "y"


def test_verification_result_contract() -> None:
    result = VerificationResult(verified=True, reason_code="note_present")
    assert result.verified is True
    for bad in (
        {"verified": "yes", "reason_code": "ok"},
        {"verified": True, "reason_code": ""},
        {"verified": True, "reason_code": "  "},
        {"verified": True, "reason_code": "Has Space"},
        {"verified": True},
    ):
        with pytest.raises(ValidationError):
            VerificationResult(**bad)


def test_fake_handler_satisfies_the_protocol() -> None:
    assert isinstance(FakeHandler(), ActionHandler)


def test_registry_lookup_is_exact() -> None:
    handler = FakeHandler("notes.add")
    registry = ActionHandlerRegistry([handler])
    assert registry.get("notes.add") is handler
    for other in ("notes.ADD", "notes", "notes.add ", "*"):
        assert registry.get(other) is None
    assert "notes.add" in registry and len(registry) == 1
    assert registry.action_names == frozenset({"notes.add"})


def test_registry_rejects_duplicates_and_non_handlers() -> None:
    with pytest.raises(ValueError, match="duplicate"):
        ActionHandlerRegistry([FakeHandler("notes.add"), FakeHandler("notes.add")])
    with pytest.raises(TypeError):
        ActionHandlerRegistry([object()])  # type: ignore[list-item]
    with pytest.raises(ValueError):
        ActionHandlerRegistry([FakeHandler("")])


def test_registry_is_immutable() -> None:
    registry = ActionHandlerRegistry([FakeHandler("notes.add")])
    with pytest.raises(TypeError):
        registry._handlers["notes.read"] = FakeHandler("notes.read")  # type: ignore[index]
    source = [FakeHandler("notes.read")]
    registry = ActionHandlerRegistry(source)
    source.append(FakeHandler("notes.add"))
    assert registry.action_names == frozenset({"notes.read"})
