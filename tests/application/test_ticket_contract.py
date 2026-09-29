"""ProductTicketCommandResult: safe vocabulary and fail-closed consistency."""

from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.commands import CommandReason, CommandStatus
from app.services.operations_tickets import (
    OperationsTicketCommandService,
    ProductTicketCommandResult,
    TicketCommandReason,
    TicketCommandStatus,
)

S, R = TicketCommandStatus, TicketCommandReason
TICKET = uuid4()


def result(**overrides) -> ProductTicketCommandResult:
    data = {"command_id": uuid4(), "status": S.VERIFIED, "reason": R.VERIFIED,
            "ticket_id": TICKET, "replayed": False, "persistence_complete": True}  # fmt: skip
    return ProductTicketCommandResult(**(data | overrides))


def test_vocabulary_matches_the_durable_command_layer_exactly() -> None:
    assert [s.value for s in S] == [s.value for s in CommandStatus]
    assert {r.value for r in R} == {r.value for r in CommandReason}
    assert not {"success", "created_successfully", "done"} & {s.value for s in S}


def test_fields_carry_nothing_internal() -> None:
    assert set(ProductTicketCommandResult.model_fields) == {
        "command_id", "status", "reason", "ticket_id", "replayed", "persistence_complete",
    }  # fmt: skip
    config = ProductTicketCommandResult.model_config
    assert config["frozen"] and config["extra"] == "forbid"


@pytest.mark.parametrize(
    "fields",
    [
        {"status": S.VERIFIED, "reason": R.VERIFIED, "ticket_id": TICKET},
        {"status": S.VERIFIED, "reason": R.VERIFIED, "ticket_id": TICKET, "replayed": True},
        {"status": S.IN_PROGRESS, "reason": None, "ticket_id": None, "replayed": True},
        {"status": S.DENIED, "reason": R.POLICY_DENIED, "ticket_id": None},
        {"status": S.AWAITING_APPROVAL, "reason": R.APPROVAL_REQUIRED, "ticket_id": None},
        {"status": S.FAILED, "reason": R.INPUT_INVALID, "ticket_id": None},
        {"status": S.REQUIRES_HUMAN, "reason": R.EXECUTION_OUTCOME_UNCERTAIN, "ticket_id": None},
        {"status": S.REQUIRES_HUMAN, "reason": R.COMMAND_EXECUTION_ERROR, "ticket_id": None},
        {"status": S.REQUIRES_HUMAN, "reason": R.COMMAND_PERSISTENCE_INCOMPLETE,
         "ticket_id": None, "persistence_complete": False},
    ],
)  # fmt: skip
def test_consistent_results(fields) -> None:
    result(**fields)


@pytest.mark.parametrize(
    "fields",
    [
        {"ticket_id": None},  # verified without a ticket
        {"status": S.REQUIRES_HUMAN, "reason": R.VERIFICATION_FAILED},  # ticket, not verified
        {"status": S.FAILED, "reason": R.INPUT_INVALID},
        {"status": S.IN_PROGRESS, "reason": R.VERIFIED, "ticket_id": None},
        {"status": S.IN_PROGRESS, "reason": R.POLICY_DENIED, "ticket_id": None},
        {"status": S.DENIED, "reason": None, "ticket_id": None},
        {"status": S.VERIFIED, "reason": R.AUDIT_INCOMPLETE},
        {"status": S.FAILED, "reason": R.VERIFIED, "ticket_id": None},
        {"persistence_complete": False},
        {"status": S.REQUIRES_HUMAN, "reason": R.COMMAND_PERSISTENCE_INCOMPLETE,
         "ticket_id": None},  # persistence_complete must be False
        {"status": S.REQUIRES_HUMAN, "reason": R.COMMAND_PERSISTENCE_INCOMPLETE,
         "ticket_id": None, "persistence_complete": False, "replayed": True},
        {"status": "success"},
        {"reason": "provider said: boom"},
        {"replayed": "yes"},
        {"ticket_id": "tkt_provider_123"},
        {"action_run_id": uuid4()},
    ],
)  # fmt: skip
def test_inconsistent_or_unsafe_results_are_rejected(fields) -> None:
    with pytest.raises(ValidationError):
        result(**fields)


def test_protocol_shape() -> None:
    class Impl:
        async def create_ticket(self, request, scope, title, description, idempotency_key):
            raise NotImplementedError

    assert isinstance(Impl(), OperationsTicketCommandService)
    assert not isinstance(object(), OperationsTicketCommandService)
