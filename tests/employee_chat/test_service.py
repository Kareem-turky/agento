"""EmployeeChatService (Task 042) on the in-memory repository: ownership, store re-checks,
idempotent turns, the Agent gate before anything is recorded, failure handling, bounded
history, and the confirm / cancel state machine through the ticket command contract."""

import asyncio
from typing import Any
from uuid import uuid4

import pytest

from app.employee_chat.errors import (
    ChatAgentDisabledError,
    ChatForbiddenError,
    ChatIdempotencyConflictError,
    ChatInvalidIdempotencyKeyError,
    ChatNotFoundError,
    ChatProposalAlreadyConfirmedError,
    ChatProposalCancelledError,
    ChatProposalNotFoundError,
    ChatTurnConflictError,
    ChatTurnInProgressError,
    ChatUnavailableError,
)
from app.employee_chat.history import bounded_history
from app.employee_chat.models import (
    MAX_ASSISTANT_CHARS,
    ChatTurn,
    ProposalState,
    ProposedTicket,
    TurnFailure,
    TurnStatus,
    utc_now,
)
from app.employee_chat.service import CONFIRM_KEY_PATTERN, EmployeeChatService
from tests.support.chat_fakes import (
    COMPANY,
    OTHER_STORE,
    STORE,
    InMemoryChatRepository,
    RecordingTickets,
    ScriptedChatRunner,
    context,
    revoke,
)
from tests.support.observability import RecordingObservability

TICKET = ProposedTicket(title="Investigate", description="Failed shipment")


def build(
    **runner: Any,
) -> tuple[EmployeeChatService, InMemoryChatRepository, ScriptedChatRunner, RecordingTickets]:
    repository, chat, tickets = (
        InMemoryChatRepository(),
        ScriptedChatRunner(**runner),
        RecordingTickets(),
    )
    return EmployeeChatService(repository, chat, tickets), repository, chat, tickets  # type: ignore[arg-type]


def run(coro: Any) -> Any:
    return asyncio.run(coro)


def test_the_confirm_key_rule_is_the_write_command_rule() -> None:
    from app.commands import IDEMPOTENCY_KEY_PATTERN

    assert CONFIRM_KEY_PATTERN == IDEMPOTENCY_KEY_PATTERN


def test_threads_are_owned_by_company_actor_and_granted_store() -> None:
    service, repository, _, _ = build()
    ctx = context()
    with pytest.raises(ChatForbiddenError):
        run(service.create_thread(ctx, OTHER_STORE))
    with pytest.raises(ChatForbiddenError):
        run(service.list_threads(ctx, OTHER_STORE))
    assert repository.threads == {}
    thread = run(service.create_thread(ctx, STORE))
    assert (
        thread.company_id,
        thread.actor_id,
        thread.actor_type,
        thread.store_id,
        thread.agent_id,
    ) == (COMPANY, "employee", "user", STORE, "operations")
    assert run(service.list_threads(ctx, STORE)) == (thread,)
    for other in (context("someone-else"), context(company="other-company")):
        with pytest.raises(ChatNotFoundError):
            run(service.get_thread(other, thread.thread_id))
        assert run(service.list_threads(other, STORE)) == ()
    with pytest.raises(ChatNotFoundError):
        run(service.get_thread(ctx, uuid4()))
    # A store that is no longer granted hides its threads, turns and proposals.
    with pytest.raises(ChatNotFoundError):
        run(service.get_thread(revoke(ctx), thread.thread_id))
    with pytest.raises(ChatNotFoundError):
        run(service.submit_turn(revoke(ctx), thread.thread_id, uuid4(), "hi"))


def test_turns_are_idempotent_and_never_rerun_the_model() -> None:
    service, _, chat, _ = build()
    ctx = context()
    thread = run(service.create_thread(ctx, STORE))
    turn_id = uuid4()
    first = run(service.submit_turn(ctx, thread.thread_id, turn_id, "hello"))
    assert first.replayed is False and first.turn.status is TurnStatus.COMPLETED
    replay = run(service.submit_turn(ctx, thread.thread_id, turn_id, "hello"))
    assert replay.replayed is True and replay.turn == first.turn
    assert len(chat.runs) == 1
    with pytest.raises(ChatTurnConflictError):
        run(service.submit_turn(ctx, thread.thread_id, turn_id, "different"))
    other = run(service.create_thread(ctx, STORE))
    with pytest.raises(ChatTurnConflictError):  # the same id in another thread
        run(service.submit_turn(ctx, other.thread_id, turn_id, "hello"))
    # Another actor cannot replay (or read) it through their own thread either.
    with pytest.raises(ChatNotFoundError):
        run(service.submit_turn(context("someone-else"), thread.thread_id, turn_id, "hello"))
    assert len(chat.runs) == 1


def test_a_pending_turn_is_in_progress() -> None:
    service, repository, chat, _ = build()
    ctx = context()
    thread = run(service.create_thread(ctx, STORE))
    turn_id = uuid4()
    run(repository.begin_turn(COMPANY, thread.thread_id, turn_id, "hello", utc_now()))
    with pytest.raises(ChatTurnInProgressError):
        run(service.submit_turn(ctx, thread.thread_id, turn_id, "hello"))
    assert chat.runs == []


def test_the_agent_gate_is_checked_before_anything_is_recorded() -> None:
    service, repository, chat, _ = build(proposal=TICKET)
    ctx = context()
    thread = run(service.create_thread(ctx, STORE))
    chat.disabled = True
    with pytest.raises(ChatAgentDisabledError):
        run(service.submit_turn(ctx, thread.thread_id, uuid4(), "create a ticket"))
    assert repository.turns == {} and repository.proposals == {} and chat.runs == []
    # Disabled between the gate and the run: the turn fails, nothing is proposed.
    chat.disabled, chat.disable_during_run = False, True
    with pytest.raises(ChatAgentDisabledError):
        run(service.submit_turn(ctx, thread.thread_id, uuid4(), "create a ticket"))
    (turn,) = repository.turns.values()
    assert turn.status is TurnStatus.FAILED and turn.failure is TurnFailure.AGENT_DISABLED
    assert turn.assistant_text is None and repository.proposals == {}


def test_a_failed_run_never_leaks_and_records_a_failed_turn() -> None:
    service, repository, chat, _ = build()
    ctx = context()
    thread = run(service.create_thread(ctx, STORE))
    chat.error = RuntimeError("provider said: sk-secret")
    with pytest.raises(ChatUnavailableError) as raised:
        run(service.submit_turn(ctx, thread.thread_id, uuid4(), "hello"))
    assert "secret" not in str(raised.value)
    (turn,) = repository.turns.values()
    assert turn.status is TurnStatus.FAILED and turn.failure is TurnFailure.RUN_FAILED
    # A failed turn never becomes context.
    chat.error = None
    run(service.submit_turn(ctx, thread.thread_id, uuid4(), "again"))
    assert chat.runs[-1][1] == ()


def test_repository_failure_is_unavailable() -> None:
    service, repository, _, _ = build()
    repository.fail = True
    with pytest.raises(ChatUnavailableError):
        run(service.create_thread(context(), STORE))


def test_history_is_the_bounded_prior_completed_turns_of_the_thread() -> None:
    service, _, chat, _ = build()
    ctx = context()
    thread = run(service.create_thread(ctx, STORE))
    other = run(service.create_thread(ctx, STORE))
    run(service.submit_turn(ctx, other.thread_id, uuid4(), "elsewhere"))
    for i in range(15):
        run(service.submit_turn(ctx, thread.thread_id, uuid4(), f"q{i}"))
    message, history = chat.runs[-1]
    assert message == "q14"
    assert [h.user_text for h in history] == [f"q{i}" for i in range(2, 14)]  # last 12
    assert all(h.assistant_text == "answer" for h in history)


def test_bounded_history_limits() -> None:
    def turn(sequence: int, text: str, status: TurnStatus = TurnStatus.COMPLETED) -> ChatTurn:
        now = utc_now()
        return ChatTurn(
            turn_id=uuid4(),
            thread_id=uuid4(),
            company_id=COMPANY,
            sequence=sequence,
            user_text=text,
            assistant_text="a" if status is TurnStatus.COMPLETED else None,
            status=status,
            failure=TurnFailure.RUN_FAILED if status is TurnStatus.FAILED else None,
            created_at=now,
            completed_at=None if status is TurnStatus.PENDING else now,
        )

    turns = [
        turn(1, "x" * 50),
        turn(2, "y" * 10),
        turn(3, "z", TurnStatus.FAILED),
        turn(4, "w", TurnStatus.PENDING),
        turn(5, "v" * 10),
    ]
    assert [h.user_text for h in bounded_history(turns)] == ["x" * 50, "y" * 10, "v" * 10]
    # Newest first while they fit; an older turn that does not fit stops the selection.
    assert [h.user_text for h in bounded_history(turns, max_chars=30)] == ["y" * 10, "v" * 10]
    assert [h.user_text for h in bounded_history(turns, max_turns=1)] == ["v" * 10]
    assert bounded_history(turns, max_turns=0) == ()


def test_a_long_answer_is_cut_to_the_stored_bound() -> None:
    service, _, _, _ = build(reply="a" * (MAX_ASSISTANT_CHARS + 50))
    ctx = context()
    thread = run(service.create_thread(ctx, STORE))
    outcome = run(service.submit_turn(ctx, thread.thread_id, uuid4(), "hello"))
    assert len(outcome.turn.assistant_text or "") == MAX_ASSISTANT_CHARS


def test_confirmation_runs_the_stored_proposal_once() -> None:
    service, repository, _, tickets = build(proposal=TICKET)
    ctx = context()
    thread = run(service.create_thread(ctx, STORE))
    outcome = run(service.submit_turn(ctx, thread.thread_id, uuid4(), "create a ticket"))
    proposal = outcome.proposal
    assert proposal is not None and proposal.state is ProposalState.PROPOSED
    assert tickets.calls == []
    with pytest.raises(ChatInvalidIdempotencyKeyError):
        run(service.confirm_ticket(ctx, proposal.proposal_id, "bad key!"))
    with pytest.raises(ChatProposalNotFoundError):
        run(service.confirm_ticket(context("someone-else"), proposal.proposal_id, "k1"))
    with pytest.raises(ChatProposalNotFoundError):
        run(service.confirm_ticket(revoke(ctx), proposal.proposal_id, "k1"))
    with pytest.raises(ChatProposalNotFoundError):
        run(service.confirm_ticket(ctx, uuid4(), "k1"))
    assert tickets.calls == []

    confirmed = run(service.confirm_ticket(ctx, proposal.proposal_id, "k1"))
    assert confirmed.proposal.state is ProposalState.SUBMITTED
    assert confirmed.proposal.command_id == confirmed.ticket.command_id
    (call,) = tickets.calls
    assert (call["title"], call["description"], call["key"]) == (
        "Investigate",
        "Failed shipment",
        "k1",
    )
    assert call["scope"].store_id == STORE and call["scope"].company_id == COMPANY
    again = run(service.confirm_ticket(ctx, proposal.proposal_id, "k1"))
    assert again.ticket.replayed is True and again.ticket.command_id == confirmed.ticket.command_id
    with pytest.raises(ChatProposalAlreadyConfirmedError):
        run(service.confirm_ticket(ctx, proposal.proposal_id, "k2"))
    with pytest.raises(ChatProposalAlreadyConfirmedError):
        run(service.cancel_ticket(ctx, proposal.proposal_id))
    assert [c["key"] for c in tickets.calls] == ["k1", "k1"]


def test_a_conflicting_key_releases_the_claim_and_unknown_outcomes_keep_it() -> None:
    service, repository, _, tickets = build(proposal=TICKET)
    ctx = context()
    thread = run(service.create_thread(ctx, STORE))
    proposal = run(service.submit_turn(ctx, thread.thread_id, uuid4(), "t")).proposal
    with pytest.raises(ChatIdempotencyConflictError):
        run(service.confirm_ticket(ctx, proposal.proposal_id, "conflicting-key"))
    assert repository.proposals[proposal.proposal_id].state is ProposalState.PROPOSED
    tickets.error = RuntimeError("timeout")
    with pytest.raises(ChatUnavailableError):
        run(service.confirm_ticket(ctx, proposal.proposal_id, "k-unknown"))
    assert repository.proposals[proposal.proposal_id].state is ProposalState.CONFIRMING
    with pytest.raises(ChatProposalAlreadyConfirmedError):  # another key: refused
        run(service.confirm_ticket(ctx, proposal.proposal_id, "k-other"))
    tickets.error = None
    retried = run(service.confirm_ticket(ctx, proposal.proposal_id, "k-unknown"))
    assert retried.proposal.state is ProposalState.SUBMITTED


def test_cancel_is_terminal() -> None:
    service, _, _, tickets = build(proposal=TICKET)
    ctx = context()
    thread = run(service.create_thread(ctx, STORE))
    proposal = run(service.submit_turn(ctx, thread.thread_id, uuid4(), "t")).proposal
    with pytest.raises(ChatProposalNotFoundError):
        run(service.cancel_ticket(context("someone-else"), proposal.proposal_id))
    assert run(service.cancel_ticket(ctx, proposal.proposal_id)).state is ProposalState.CANCELLED
    assert run(service.cancel_ticket(ctx, proposal.proposal_id)).state is ProposalState.CANCELLED
    with pytest.raises(ChatProposalCancelledError):
        run(service.confirm_ticket(ctx, proposal.proposal_id, "k1"))
    assert tickets.calls == []


def test_observations_are_bounded() -> None:
    observability = RecordingObservability()
    repository, chat = InMemoryChatRepository(), ScriptedChatRunner(proposal=TICKET)
    tickets = RecordingTickets()
    service = EmployeeChatService(
        repository,
        chat,
        tickets,  # type: ignore[arg-type]
        observability=observability,
    )
    ctx = context()
    thread = run(service.create_thread(ctx, STORE))
    proposal = run(service.submit_turn(ctx, thread.thread_id, uuid4(), "secret-text")).proposal
    run(service.confirm_ticket(ctx, proposal.proposal_id, "secret-key"))
    with pytest.raises(ChatForbiddenError):
        run(service.create_thread(ctx, OTHER_STORE))
    text = repr([(r.operation, r.attributes) for r in observability.records])
    for value in (
        "secret-text",
        "secret-key",
        "Investigate",
        "Failed shipment",
        str(thread.thread_id),
        str(proposal.proposal_id),
    ):
        assert value not in text, value
    operations = {r.operation.value for r in observability.records}
    assert {"chat.thread", "chat.turn", "chat.ticket_proposal"} <= operations
    assert all(r.exited and len(r.outcomes) == 1 for r in observability.records)
