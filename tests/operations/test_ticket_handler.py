"""CreateOperationalTicketHandler in isolation (validation, context, error mapping)."""

from datetime import UTC, datetime
from uuid import UUID

import pytest
from pydantic import ValidationError

from app.commerce.domain import Ticket
from app.execution import (
    ActionExecutionContext,
    ActionHandler,
    ExecutionFailedWithoutEffect,
    ExecutionOutcomeUncertain,
    ExecutionResult,
)
from app.governance import ActionRisk, ActionScopeRequirement
from app.integrations.commerce import (
    IntegrationWriteRejectedError,
    IntegrationWriteUncertainError,
)
from app.operations import (
    CREATE_TICKET_ACTION,
    CreateOperationalTicketHandler,
    CreateOperationalTicketInput,
)
from tests.execution.fakes import run
from tests.operations.helpers import COMPANY, PARAMS, STORE

RUN_ID = UUID(int=42)
PROVIDER_DETAIL = "PROVIDER-PROVIDER_DETAIL-DETAIL-0000"


def ctx(**overrides) -> ActionExecutionContext:
    data = {
        "run_id": RUN_ID, "request_id": UUID(int=1), "action_name": CREATE_TICKET_ACTION.name,
        "actor_id": "ops-user-1", "actor_type": "user", "company_id": COMPANY,
        "store_id": STORE, "channel": "api", "session_id": None,
    }  # fmt: skip
    data.update(overrides)
    return ActionExecutionContext(**data)


def ticket(**overrides) -> Ticket:
    data = {
        "id": UUID(int=7), "company_id": UUID(COMPANY), "store_id": UUID(STORE),
        "title": PARAMS["title"], "description": PARAMS["description"], "status": "open",
        "created_at": datetime(2026, 3, 2, tzinfo=UTC),
    }  # fmt: skip
    data.update(overrides)
    return Ticket.model_validate(data)


class FakeTicketing:
    def __init__(self, *, create=None, found=None) -> None:
        self.create = create
        self.found = found
        self.calls: list[dict] = []

    async def create_ticket(self, **kwargs):
        self.calls.append(kwargs)
        if isinstance(self.create, BaseException):
            raise self.create
        return self.create

    async def get_ticket(self, ticket_id):
        return self.found

    async def find_ticket_by_correlation(self, correlation_id):
        return self.found


VALID = CreateOperationalTicketInput(**PARAMS)


def test_action_definition() -> None:
    assert CREATE_TICKET_ACTION.name == "operations.ticket.create"
    assert CREATE_TICKET_ACTION.required_permission == "tickets.create"
    assert CREATE_TICKET_ACTION.risk is ActionRisk.LOW_RISK_WRITE
    assert CREATE_TICKET_ACTION.scope_requirement is ActionScopeRequirement.STORE


def test_handler_satisfies_the_protocol() -> None:
    handler = CreateOperationalTicketHandler(FakeTicketing())
    assert isinstance(handler, ActionHandler)
    assert handler.action_name == "operations.ticket.create"


def test_input_contains_only_business_content() -> None:
    assert set(CreateOperationalTicketInput.model_fields) == {"title", "description"}
    assert CreateOperationalTicketInput.model_config["frozen"] is True
    assert CreateOperationalTicketInput.model_config["extra"] == "forbid"


def test_validate_trims_and_is_side_effect_free() -> None:
    integration = FakeTicketing()
    handler = CreateOperationalTicketHandler(integration)
    data = handler.validate({"title": "  Parcel delayed ", "description": " x "})
    assert (data.title, data.description) == ("Parcel delayed", "x")
    assert integration.calls == []


@pytest.mark.parametrize(
    "params",
    [
        {"title": "", "description": "d"},
        {"title": "   ", "description": "d"},
        {"title": "t" * 161, "description": "d"},
        {"title": "t", "description": ""},
        {"title": "t", "description": "d" * 4001},
        {"title": "t"},
        {"title": 5, "description": "d"},
        {"title": "t", "description": "d", "company_id": COMPANY},
    ],
)
def test_validate_rejects_bad_input(params) -> None:
    with pytest.raises(ValidationError):
        CreateOperationalTicketHandler(FakeTicketing()).validate(params)


def test_boundary_lengths_are_accepted() -> None:
    CreateOperationalTicketHandler(FakeTicketing()).validate(
        {"title": "t" * 160, "description": "d" * 4000}
    )


def test_execute_uses_only_trusted_context() -> None:
    integration = FakeTicketing(create=ticket())
    result = run(CreateOperationalTicketHandler(integration).execute(ctx(), VALID))
    assert result == ExecutionResult(reference_id=str(UUID(int=7)))
    assert integration.calls == [
        {
            "company_id": UUID(COMPANY), "store_id": UUID(STORE), "title": VALID.title,
            "description": VALID.description, "correlation_id": RUN_ID,
        }
    ]  # fmt: skip


@pytest.mark.parametrize(
    "overrides",
    [{"company_id": "company-1"}, {"store_id": "store-a"}, {"store_id": None}],
)
def test_malformed_trusted_scope_fails_before_any_write(overrides) -> None:
    integration = FakeTicketing(create=ticket())
    with pytest.raises(ExecutionFailedWithoutEffect):
        run(CreateOperationalTicketHandler(integration).execute(ctx(**overrides), VALID))
    assert integration.calls == []


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (IntegrationWriteRejectedError("ticket"), ExecutionFailedWithoutEffect),
        (IntegrationWriteUncertainError("ticket"), ExecutionOutcomeUncertain),
        (RuntimeError(f"socket reset {PROVIDER_DETAIL}"), ExecutionOutcomeUncertain),
        (TimeoutError(PROVIDER_DETAIL), ExecutionOutcomeUncertain),
    ],
)
def test_write_errors_are_mapped_without_provider_text(error, expected) -> None:
    handler = CreateOperationalTicketHandler(FakeTicketing(create=error))
    with pytest.raises(expected) as info:
        run(handler.execute(ctx(), VALID))
    assert type(info.value) is expected
    assert PROVIDER_DETAIL not in str(info.value)
    assert info.value.__cause__ is None


def test_non_ticket_answer_is_uncertain() -> None:
    handler = CreateOperationalTicketHandler(FakeTicketing(create={"id": "tkt_1"}))
    with pytest.raises(ExecutionOutcomeUncertain):
        run(handler.execute(ctx(), VALID))


@pytest.mark.parametrize(
    ("found", "receipt", "verified", "code"),
    [
        (None, None, False, "ticket_missing"),
        (ticket(), None, True, "ticket_present"),
        (ticket(), ExecutionResult(reference_id=str(UUID(int=7))), True, "ticket_present"),
        (ticket(), ExecutionResult(reference_id=str(UUID(int=8))), False,
         "ticket_reference_mismatch"),
        (ticket(title="Other"), None, False, "ticket_mismatch"),
        (ticket(description="Other"), None, False, "ticket_mismatch"),
        (ticket(status="resolved"), None, False, "ticket_mismatch"),
        (ticket(store_id=UUID(int=3)), None, False, "ticket_mismatch"),
        (ticket(company_id=UUID(int=3)), None, False, "ticket_mismatch"),
    ],
)  # fmt: skip
def test_verify_compares_the_reread_with_trusted_scope_and_input(
    found, receipt, verified, code
) -> None:
    handler = CreateOperationalTicketHandler(FakeTicketing(found=found))
    result = run(handler.verify(ctx(), VALID, receipt))
    assert (result.verified, result.reason_code) == (verified, code)


def test_verify_with_malformed_scope_is_not_verified() -> None:
    handler = CreateOperationalTicketHandler(FakeTicketing(found=ticket()))
    result = run(handler.verify(ctx(company_id="company-1"), VALID, None))
    assert (result.verified, result.reason_code) == (False, "ticket_scope_invalid")
