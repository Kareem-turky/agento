"""Task 042 on migrated PostgreSQL: the REAL PostgresEmployeeChatRepository behind the real
EmployeeChatService (a counting fake chat runner and ticket service; no model, no network
beyond the local database).

Concurrent identical turn submissions are ONE turn and ONE model run; concurrent distinct
turns get unique increasing sequences; confirm-vs-cancel races have exactly one outcome;
concurrent confirmations with the same key or different keys reach the ticket service
under exactly one key; CHECK constraints refuse inconsistent rows; every query is company
scoped in SQL; the downgrade drops only the chat tables (see test_product_migrations)."""

import asyncio
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa

from app.context.models import ActorContext, RequestContext
from app.employee_chat.errors import (
    ChatProposalAlreadyConfirmedError,
    ChatProposalCancelledError,
    ChatTurnInProgressError,
)
from app.employee_chat.models import ChatRunResult, ProposedTicket
from app.employee_chat.service import EmployeeChatService
from app.persistence import PostgresEmployeeChatRepository, create_product_engine
from app.persistence.database import create_session_factory
from app.services.operations_tickets import (
    ProductTicketCommandResult,
    TicketCommandReason,
    TicketCommandStatus,
)

pytestmark = pytest.mark.integration

STORE = "11111111-1111-4111-8111-111111111111"


class SlowRunner:
    def __init__(self, proposal: bool = False) -> None:
        self.runs = 0
        self.proposal = proposal

    async def ensure_runnable(self, scope: Any) -> None:
        return None

    async def run_chat(self, request: Any, scope: Any, message: str, history: Any) -> Any:
        self.runs += 1
        await asyncio.sleep(0.05)
        return ChatRunResult(
            message=f"answer {len(history)}",
            proposal=ProposedTicket(title="T", description="D") if self.proposal else None,
        )


class CountingTickets:
    def __init__(self) -> None:
        self.keys: list[str] = []
        self.commands: dict[str, UUID] = {}

    async def create_ticket(
        self, context: Any, scope: Any, title: str, description: str, key: str
    ) -> ProductTicketCommandResult:
        self.keys.append(key)
        await asyncio.sleep(0.05)
        replayed = key in self.commands
        command_id = self.commands.setdefault(key, uuid4())
        return ProductTicketCommandResult(
            command_id=command_id,
            status=TicketCommandStatus.VERIFIED,
            reason=TicketCommandReason.VERIFIED,
            ticket_id=uuid4(),
            replayed=replayed,
            persistence_complete=True,
        )


def context(company: str, actor: str = "employee") -> RequestContext:
    return RequestContext(
        actor=ActorContext(
            actor_id=actor,
            actor_type="user",
            company_id=company,
            permissions=frozenset({"tickets.create"}),
            store_ids=frozenset({STORE}),
        ),
        channel="api",
    )


def scenario(database_url: str, body: Any, *, proposal: bool = False) -> Any:
    async def main() -> Any:
        engine = create_product_engine(database_url)
        repository = PostgresEmployeeChatRepository(create_session_factory(engine))
        runner, tickets = SlowRunner(proposal), CountingTickets()
        service = EmployeeChatService(repository, runner, tickets)  # type: ignore[arg-type]
        try:
            return await body(service, runner, tickets, str(uuid4()))
        finally:
            await engine.dispose()

    return asyncio.run(main())


def rows(engine: sa.Engine, sql: str, **params: Any) -> list[dict[str, Any]]:
    with engine.connect() as connection:
        return [dict(r) for r in connection.execute(sa.text(sql), params).mappings()]


def test_concurrent_identical_turns_are_one_turn_and_one_run(migrated, engine) -> None:
    async def body(service, runner, tickets, company):
        ctx = context(company)
        thread = await service.create_thread(ctx, STORE)
        turn_id = uuid4()
        results = await asyncio.gather(
            *(service.submit_turn(ctx, thread.thread_id, turn_id, "hello") for _ in range(8)),
            return_exceptions=True,
        )
        return company, results, runner.runs

    company, results, runs = scenario(migrated, body)
    assert runs == 1
    completed = [r for r in results if not isinstance(r, BaseException)]
    in_progress = [r for r in results if isinstance(r, ChatTurnInProgressError)]
    assert len(completed) + len(in_progress) == 8
    assert [r.replayed for r in completed].count(False) == 1
    assert rows(
        engine, "SELECT count(*) AS n FROM product.chat_turns WHERE company_id = :c", c=company
    ) == [{"n": 1}]


def test_concurrent_distinct_turns_get_unique_sequences(migrated, engine) -> None:
    async def body(service, runner, tickets, company):
        ctx = context(company)
        thread = await service.create_thread(ctx, STORE)
        await asyncio.gather(
            *(service.submit_turn(ctx, thread.thread_id, uuid4(), f"m{i}") for i in range(10))
        )
        return company, thread.thread_id

    company, thread_id = scenario(migrated, body)
    sequences = [
        r["sequence"]
        for r in rows(
            engine,
            "SELECT sequence FROM product.chat_turns WHERE company_id = :c ORDER BY sequence",
            c=company,
        )
    ]
    assert sequences == list(range(1, 11))
    (thread,) = rows(
        engine,
        "SELECT next_turn_sequence FROM product.chat_threads WHERE thread_id = :t",
        t=thread_id,
    )
    assert thread["next_turn_sequence"] == 11


def test_confirm_vs_cancel_has_exactly_one_outcome(migrated, engine) -> None:
    async def body(service, runner, tickets, company):
        ctx = context(company)
        thread = await service.create_thread(ctx, STORE)
        outcomes = []
        for _ in range(6):
            proposal = (
                await service.submit_turn(ctx, thread.thread_id, uuid4(), "ticket please")
            ).proposal
            outcomes.append(
                await asyncio.gather(
                    service.confirm_ticket(ctx, proposal.proposal_id, f"k-{uuid4()}"),
                    service.cancel_ticket(ctx, proposal.proposal_id),
                    return_exceptions=True,
                )
            )
        return company, outcomes, tickets

    company, outcomes, tickets = scenario(migrated, body, proposal=True)
    confirmed = 0
    for confirm, cancel in outcomes:
        confirm_won = not isinstance(confirm, BaseException)
        cancel_won = not isinstance(cancel, BaseException)
        assert confirm_won != cancel_won  # exactly one outcome
        if confirm_won:
            confirmed += 1
            assert isinstance(cancel, ChatProposalAlreadyConfirmedError)
        else:
            assert isinstance(confirm, ChatProposalCancelledError)
    assert len(tickets.keys) == confirmed
    states = sorted(
        r["state"]
        for r in rows(
            engine,
            "SELECT state FROM product.chat_action_proposals WHERE company_id = :c",
            c=company,
        )
    )
    assert states.count("submitted") == confirmed
    assert states.count("cancelled") == 6 - confirmed


def test_concurrent_confirmations_bind_one_key(migrated, engine) -> None:
    async def body(service, runner, tickets, company):
        ctx = context(company)
        thread = await service.create_thread(ctx, STORE)
        proposal = (
            await service.submit_turn(ctx, thread.thread_id, uuid4(), "ticket please")
        ).proposal
        results = await asyncio.gather(
            *(service.confirm_ticket(ctx, proposal.proposal_id, f"key-{i % 3}") for i in range(9)),
            return_exceptions=True,
        )
        return results, tickets

    results, tickets = scenario(migrated, body, proposal=True)
    winners = [r for r in results if not isinstance(r, BaseException)]
    refused = [r for r in results if isinstance(r, ChatProposalAlreadyConfirmedError)]
    assert len(winners) + len(refused) == 9
    assert len(set(tickets.keys)) == 1  # only ONE key ever reached the ticket service
    assert len({w.ticket.command_id for w in winners}) == 1
    assert len({w.proposal.confirm_key_hash for w in winners}) == 1


def test_company_scope_is_in_sql_and_checks_refuse_inconsistent_rows(migrated, engine) -> None:
    async def body(service, runner, tickets, company):
        ctx = context(company)
        thread = await service.create_thread(ctx, STORE)
        outcome = await service.submit_turn(ctx, thread.thread_id, uuid4(), "hello")
        other = context(str(uuid4()))
        repository = service._repository
        assert (
            await repository.get_thread(
                other.actor.company_id, "employee", "user", thread.thread_id
            )
            is None
        )
        assert await repository.get_turn(other.actor.company_id, outcome.turn.turn_id) is None
        assert await repository.list_turns(other.actor.company_id, thread.thread_id, 10) == ()
        return thread.thread_id

    thread_id = scenario(migrated, body)
    now = datetime.now(UTC)
    bad = [
        ("UPDATE product.chat_threads SET agent_id = 'cx' WHERE thread_id = :t", {"t": thread_id}),
        (
            "UPDATE product.chat_turns SET status = 'completed', assistant_text = NULL "
            "WHERE thread_id = :t",
            {"t": thread_id},
        ),
        ("UPDATE product.chat_turns SET user_text = '' WHERE thread_id = :t", {"t": thread_id}),
        (
            "INSERT INTO product.chat_action_proposals SELECT gen_random_uuid(), thread_id, "
            "turn_id, company_id, 'operations.refund.create', 't', 'd', 'proposed', NULL, NULL, "
            ":n, :n FROM product.chat_turns WHERE thread_id = :t",
            {"t": thread_id, "n": now},
        ),
        (
            "INSERT INTO product.chat_action_proposals SELECT gen_random_uuid(), thread_id, "
            "turn_id, company_id, 'operations.ticket.create', 't', 'd', 'submitted', NULL, NULL, "
            ":n, :n FROM product.chat_turns WHERE thread_id = :t",
            {"t": thread_id, "n": now},
        ),
    ]
    for sql, params in bad:
        with pytest.raises(sa.exc.IntegrityError), engine.begin() as connection:
            connection.execute(sa.text(sql), params)
