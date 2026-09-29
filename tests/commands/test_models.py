"""Command status vocabulary, records and results."""

from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.commands import (
    ACTION_RUN_STATUS_TO_COMMAND,
    TERMINAL_STATUSES,
    ClaimOutcome,
    ClaimResult,
    CommandReason,
    CommandStatus,
    WriteCommandOutcome,
    WriteCommandRecord,
    WriteCommandResult,
)
from app.execution import ActionRunReason, ActionRunStatus
from tests.commands.fakes import T0


def record(**overrides) -> WriteCommandRecord:
    data = {
        "command_id": uuid4(), "company_id": "c", "actor_id": "a", "store_id": "s",
        "action_name": "notes.add", "status": CommandStatus.IN_PROGRESS, "reason": None,
        "action_run_id": None, "execution_reference_id": None, "audit_complete": None,
        "created_at": T0, "updated_at": T0,
    }  # fmt: skip
    return WriteCommandRecord(**(data | overrides))


def test_status_vocabulary() -> None:
    assert [s.value for s in CommandStatus] == [
        "in_progress", "denied", "awaiting_approval", "failed", "requires_human", "verified",
    ]  # fmt: skip
    assert "success" not in {s.value for s in CommandStatus}
    assert TERMINAL_STATUSES == set(CommandStatus) - {CommandStatus.IN_PROGRESS}


def test_every_action_run_status_maps_to_the_same_named_command_status() -> None:
    assert set(ACTION_RUN_STATUS_TO_COMMAND) == set(ActionRunStatus)
    for run_status, command_status in ACTION_RUN_STATUS_TO_COMMAND.items():
        assert command_status.value == run_status.value
    assert CommandStatus.IN_PROGRESS not in ACTION_RUN_STATUS_TO_COMMAND.values()


def test_every_action_run_reason_is_a_command_reason() -> None:
    assert {r.value for r in ActionRunReason} <= {r.value for r in CommandReason}
    extra = {r.value for r in CommandReason} - {r.value for r in ActionRunReason}
    assert extra == {"command_execution_error", "command_persistence_incomplete"}


def test_application_facing_models_never_carry_hashes_keys_or_parameters() -> None:
    forbidden = {"idempotency_key", "idempotency_key_hash", "request_fingerprint", "parameters",
                 "title", "description", "permissions", "role_ids"}  # fmt: skip
    for model in (WriteCommandRecord, WriteCommandResult, WriteCommandOutcome, ClaimResult):
        assert not set(model.model_fields) & forbidden, model
        assert model.model_config["frozen"] and model.model_config["extra"] == "forbid"


def test_result_fields() -> None:
    assert set(WriteCommandResult.model_fields) == {
        "command_id", "action_name", "status", "reason", "action_run_id",
        "execution_reference_id", "audit_complete", "replayed", "persistence_complete",
    }  # fmt: skip


@pytest.mark.parametrize(
    "overrides",
    [
        {"reason": CommandReason.VERIFIED},  # in progress with an outcome
        {"action_run_id": uuid4()},
        {"audit_complete": True},
        {"status": CommandStatus.FAILED},  # terminal without a reason
        {"status": CommandStatus.VERIFIED, "reason": CommandReason.VERIFIED},  # no run id
        {"status": CommandStatus.VERIFIED, "reason": CommandReason.VERIFIED,
         "action_run_id": uuid4(), "audit_complete": False},
        {"status": CommandStatus.VERIFIED, "reason": CommandReason.AUDIT_INCOMPLETE,
         "action_run_id": uuid4(), "audit_complete": True},
        {"status": CommandStatus.FAILED, "reason": CommandReason.VERIFIED},
        {"status": "success"},
        {"reason": "made_up"},
        {"execution_reference_id": "has spaces"},
        {"created_at": T0.replace(tzinfo=None)},
    ],
)  # fmt: skip
def test_inconsistent_or_unknown_records_are_rejected(overrides) -> None:
    with pytest.raises(ValidationError):
        record(**overrides)


def test_consistent_records() -> None:
    record()
    ref = "0b0b0b0b-0000-4000-8000-000000000001"
    record(status=CommandStatus.VERIFIED, reason=CommandReason.VERIFIED, action_run_id=uuid4(),
           audit_complete=True, execution_reference_id=ref)  # fmt: skip
    record(status=CommandStatus.REQUIRES_HUMAN, reason=CommandReason.COMMAND_EXECUTION_ERROR)


def test_outcomes_are_terminal() -> None:
    with pytest.raises(ValidationError):
        WriteCommandOutcome(status=CommandStatus.IN_PROGRESS, reason=CommandReason.VERIFIED)


def test_claim_result_shape() -> None:
    new = record()
    assert ClaimResult(outcome=ClaimOutcome.NEW, record=new).record == new
    ClaimResult(outcome=ClaimOutcome.CONFLICT)
    with pytest.raises(ValidationError):
        ClaimResult(outcome=ClaimOutcome.CONFLICT, record=new)
    with pytest.raises(ValidationError):
        ClaimResult(outcome=ClaimOutcome.REPLAY)
    done = record(status=CommandStatus.FAILED, reason=CommandReason.INPUT_INVALID,
                  action_run_id=uuid4(), audit_complete=True)  # fmt: skip
    with pytest.raises(ValidationError):
        ClaimResult(outcome=ClaimOutcome.NEW, record=done)
