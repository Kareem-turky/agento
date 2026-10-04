"""EMPLOYEE CHAT ACCEPTANCE (Task 042): the REAL installation (``create_deployment_app``, the
``mock`` business backend, Product API-key auth, the migrated PostgreSQL) with the
deterministic LOCAL/TEST-only demo model (no network, no provider).

* Identity and isolation: a thread belongs to company + actor + store. Another actor's,
  another company's or an unknown thread is 404; a store that is not granted is 403 (and
  nothing is created or run).
* A real multi-turn conversation with the existing Operations Agent: the analysis comes
  from the deterministic daily report; earlier turns of the SAME thread reach the model as
  bounded context.
* Idempotent turns: a replay returns the stored answer WITHOUT a model call; the same turn
  id with a different message is 409; errors never echo submitted values.
* The model may only PROPOSE ``operations.ticket.create``: a turn writes no WriteCommand,
  audit event or ticket. Only an explicit confirmation (proposal id + exactly one
  Idempotency-Key, never the ticket text) runs the existing governed ticket WriteCommand
  path; a same-key retry replays it; another key, a cancelled proposal, or a second
  confirmation never creates a second command.
* A disabled Operations Agent refuses a turn (409) before any model call, tool call,
  proposal or completed turn.
* Product observability records bounded chat events, never message text, titles or ids.
"""

from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from app.runtime.demo_model import DemoOperationsModel
from tests.support.canonical_mock import BUSINESS_DATE, COMPANY, NORTH, SOUTH
from tests.support.product_core import (
    DECIDER,
    NO_CREDENTIALS,
    OPERATOR,
    RESTRICTED,
    CoreInstallation,
    count,
    rows,
    wipe_agent_configuration,
)
from tests.support.scripted_tool_model import CallTool, Reply, ScriptedToolModel

pytestmark = pytest.mark.integration

THREADS, THREAD, TURNS = "/api/v1/chat/threads", "/api/v1/chat/thread", "/api/v1/chat/turns"
CONFIRM = "/api/v1/chat/ticket-proposals/confirm"
CANCEL = "/api/v1/chat/ticket-proposals/cancel"
OPS = {"agent_id": "operations"}
ANALYZE = f"Analyze operations for {BUSINESS_DATE}."
TICKET_TITLE = "Investigate failed shipment"
TICKET_DESCRIPTION = "Review the failed shipment found in the demo operations report."
TICKET_REQUEST = (
    f'Create an operational ticket titled "{TICKET_TITLE}" with description "{TICKET_DESCRIPTION}"'
)
PREPARED = "Ticket prepared. Confirm the action to create it."
MARKER = "chat-secret-marker-7f3a"


@dataclass
class CountingDemoModel(DemoOperationsModel):
    """The demo model, counting every model request (one per Agno model call)."""

    seen: list[list[tuple[str, str]]] = field(default_factory=list)

    def _respond(self, kwargs: dict[str, Any]) -> Any:
        self.seen.append(
            [
                (str(getattr(m, "role", "")), str(getattr(m, "content", "") or ""))
                for m in kwargs.get("messages") or []
            ]
        )
        return super()._respond(kwargs)


@pytest.fixture(autouse=True)
def no_agent_override(engine: sa.Engine) -> Any:
    wipe_agent_configuration(engine)
    yield
    wipe_agent_configuration(engine)


def thread_for(client: TestClient, headers: Any = OPERATOR, store: str = SOUTH) -> str:
    created = client.post(THREADS, headers=headers, json={"store_id": store})
    assert created.status_code == 201, created.text
    return created.json()["thread"]["thread_id"]


def turn(
    client: TestClient,
    thread_id: str,
    message: str,
    turn_id: str | None = None,
    headers: Any = OPERATOR,
) -> Any:
    return client.post(
        TURNS,
        headers=headers,
        json={"thread_id": thread_id, "turn_id": turn_id or str(uuid4()), "message": message},
    )


def side_effects(engine: sa.Engine) -> dict[str, int]:
    return {t: count(engine, t, company_id=COMPANY) for t in ("write_commands", "audit_events")}


# ----- identity, isolation, no model call without a message ----------------------------------


def test_threads_are_private_to_company_actor_and_store(
    core: CoreInstallation, engine: sa.Engine
) -> None:
    model = CountingDemoModel()
    with TestClient(core.app(model=model)) as client:
        for method, path in (
            ("get", THREADS),
            ("post", THREADS),
            ("get", THREAD),
            ("get", TURNS),
            ("post", TURNS),
            ("post", CONFIRM),
            ("post", CANCEL),
        ):
            assert getattr(client, method)(path, headers=NO_CREDENTIALS).status_code == 401
        # A store that is not granted: 403, nothing created.
        before = count(engine, "chat_threads", company_id=COMPANY)
        denied = client.post(THREADS, headers=OPERATOR, json={"store_id": NORTH})
        assert denied.status_code == 403 and denied.json() == {"detail": "Forbidden"}
        assert count(engine, "chat_threads", company_id=COMPANY) == before
        assert client.get(THREADS, headers=OPERATOR, params={"store_id": NORTH}).status_code == 403

        thread_id = thread_for(client)
        stored = rows(
            engine, "SELECT * FROM product.chat_threads WHERE thread_id = :t", t=thread_id
        )[0]
        assert stored["company_id"] == COMPANY and stored["store_id"] == SOUTH
        assert stored["actor_id"] == "core-operator" and stored["agent_id"] == "operations"
        listed = client.get(THREADS, headers=OPERATOR, params={"store_id": SOUTH}).json()
        assert thread_id in {t["thread_id"] for t in listed["threads"]}

        # Another actor of the same company and store, another store's actor, unknown id:
        # the same 404 (no existence oracle), and they never list it.
        for headers in (DECIDER, RESTRICTED):
            for path in (THREAD, TURNS):
                answer = client.get(path, headers=headers, params={"thread_id": thread_id})
                assert answer.status_code == 404
                assert answer.json() == {"detail": "Chat thread not found"}
            assert turn(client, thread_id, "hello", headers=headers).status_code == 404
        decider_threads = client.get(THREADS, headers=DECIDER, params={"store_id": SOUTH})
        assert thread_id not in {t["thread_id"] for t in decider_threads.json()["threads"]}
        assert (
            client.get(THREAD, headers=OPERATOR, params={"thread_id": str(uuid4())}).status_code
            == 404
        )
        # Opening, listing and reading threads never calls the model.
        assert model.seen == []


def test_another_company_cannot_read_the_thread(core: CoreInstallation) -> None:
    # ANOTHER company's installation on the SAME database (same keys and code).
    with TestClient(core.app(model=CountingDemoModel())) as client:
        thread_id = thread_for(client)
        assert turn(client, thread_id, ANALYZE).status_code == 201
    with TestClient(core.other_company_app("other-chat-company")) as other:
        # The other installation has no business backend: no Agent; the repository
        # still answers reads, scoped by ITS company.
        answer = other.get(THREAD, headers=OPERATOR, params={"thread_id": thread_id})
        assert answer.status_code == 404
        assert (
            other.get(THREADS, headers=OPERATOR, params={"store_id": SOUTH}).json()["threads"] == []
        )


# ----- multi-turn analysis, idempotent turns ----------------------------------------------------


def test_multi_turn_analysis_with_idempotent_replay(
    core: CoreInstallation, engine: sa.Engine
) -> None:
    model = CountingDemoModel()
    with TestClient(core.app(model=model)) as client:
        thread_id = thread_for(client)
        effects = side_effects(engine)
        turn_id = str(uuid4())
        first = turn(client, thread_id, ANALYZE, turn_id)
        assert first.status_code == 201, first.text
        body = first.json()
        assert body["replayed"] is False and body["proposal"] is None
        answer = body["turn"]["assistant_text"]
        assert body["turn"]["status"] == "completed" and body["turn"]["sequence"] == 1
        assert f"Daily operations report for {BUSINESS_DATE}" in answer
        calls = len(model.seen)
        assert calls == 2  # the report tool call, then the summary

        # Completed replay: the stored answer, no model call.
        replay = turn(client, thread_id, ANALYZE, turn_id)
        assert replay.status_code == 200
        assert replay.json()["replayed"] is True
        assert replay.json()["turn"]["assistant_text"] == answer
        assert len(model.seen) == calls
        # The same id with a different message: 409, never echoing it.
        conflict = turn(client, thread_id, f"{MARKER} different", turn_id)
        assert conflict.status_code == 409 and MARKER not in conflict.text
        assert conflict.json() == {"detail": "Chat turn conflict"}
        assert len(model.seen) == calls

        # A second turn: the first exchange is bounded CONTEXT for the model.
        second = turn(client, thread_id, "What should I look at first?")
        assert second.status_code == 201 and second.json()["turn"]["sequence"] == 2
        latest = model.seen[-1]
        assert ("user", ANALYZE) in latest and ("assistant", answer) in latest
        assert latest[-1] == ("user", "What should I look at first?")

        detail = client.get(THREAD, headers=OPERATOR, params={"thread_id": thread_id}).json()
        assert [t["sequence"] for t in detail["turns"]] == [1, 2]
        assert detail["proposals"] == []
        # Analysis turns write nothing operational.
        assert side_effects(engine) == effects
        assert count(engine, "chat_turns", thread_id=thread_id) == 2


@pytest.mark.parametrize(
    "payload",
    [
        {"message": ""},
        {"message": "   "},
        {"message": "x" * 8001},
        {"message": 7},
        {"message": f"{MARKER}", "store_id": SOUTH},
        {"message": f"{MARKER}", "actor_id": "root"},
    ],
)
def test_invalid_turns_are_refused_without_echo(
    core: CoreInstallation, payload: dict[str, Any]
) -> None:
    model = CountingDemoModel()
    with TestClient(core.app(model=model)) as client:
        thread_id = thread_for(client)
        answer = client.post(
            TURNS,
            headers=OPERATOR,
            json={"thread_id": thread_id, "turn_id": str(uuid4()), **payload},
        )
        assert answer.status_code == 422
        assert MARKER not in answer.text and "xxxxxxxx" not in answer.text
        assert model.seen == []


# ----- proposal -> explicit governed confirmation ---------------------------------------------


def test_a_proposal_executes_nothing_until_an_explicit_confirmation(
    core: CoreInstallation, engine: sa.Engine
) -> None:
    model = CountingDemoModel()
    with TestClient(core.app(model=model)) as client:
        desk = core.desks[-1]
        thread_id = thread_for(client)
        effects, tickets = side_effects(engine), desk.ticket_count

        proposed = turn(client, thread_id, TICKET_REQUEST)
        assert proposed.status_code == 201, proposed.text
        body = proposed.json()
        assert body["turn"]["assistant_text"] == PREPARED
        assert "created" not in body["turn"]["assistant_text"].lower()
        proposal = body["proposal"]
        assert proposal["action"] == "operations.ticket.create"
        assert proposal["title"] == TICKET_TITLE
        assert proposal["description"] == TICKET_DESCRIPTION
        assert proposal["state"] == "proposed" and proposal["command_id"] is None
        # The proposal wrote nothing operational.
        assert side_effects(engine) == effects and desk.ticket_count == tickets

        proposal_id = proposal["proposal_id"]
        # The confirmation needs exactly one Idempotency-Key and takes only the id.
        assert (
            client.post(CONFIRM, headers=OPERATOR, json={"proposal_id": proposal_id}).status_code
            == 400
        )
        doubled = client.post(
            CONFIRM,
            headers=[*OPERATOR.items(), ("Idempotency-Key", "a"), ("Idempotency-Key", "b")],
            json={"proposal_id": proposal_id},
        )
        assert doubled.status_code == 400
        resent = client.post(
            CONFIRM,
            headers=OPERATOR | {"Idempotency-Key": "k-resent"},
            json={"proposal_id": proposal_id, "title": MARKER},
        )
        assert resent.status_code == 422 and MARKER not in resent.text
        invalid = client.post(
            CONFIRM,
            headers=OPERATOR | {"Idempotency-Key": "bad key!"},
            json={"proposal_id": proposal_id},
        )
        assert invalid.status_code == 400 and "bad key" not in invalid.text
        assert side_effects(engine) == effects and desk.ticket_count == tickets
        # Another actor cannot confirm or cancel it (indistinguishable from unknown).
        for headers in (DECIDER, RESTRICTED):
            foreign = client.post(
                CONFIRM,
                headers=headers | {"Idempotency-Key": "k-foreign"},
                json={"proposal_id": proposal_id},
            )
            assert foreign.status_code == 404
            assert foreign.json() == {"detail": "Ticket proposal not found"}
            assert (
                client.post(CANCEL, headers=headers, json={"proposal_id": proposal_id}).status_code
                == 404
            )

        key = f"chat-confirm-{uuid4()}"
        confirmed = client.post(
            CONFIRM, headers=OPERATOR | {"Idempotency-Key": key}, json={"proposal_id": proposal_id}
        )
        assert confirmed.status_code == 201, confirmed.text
        result = confirmed.json()
        assert result["ticket"]["status"] == "verified" and result["ticket"]["replayed"] is False
        assert result["ticket"]["ticket_id"] is not None
        assert result["proposal"]["state"] == "submitted"
        command_id = result["ticket"]["command_id"]
        assert result["proposal"]["command_id"] == command_id
        assert desk.ticket_count == tickets + 1
        command = rows(
            engine, "SELECT * FROM product.write_commands WHERE command_id = :c", c=command_id
        )[0]
        assert command["action_name"] == "operations.ticket.create"
        assert command["company_id"] == COMPANY and command["store_id"] == SOUTH
        assert command["actor_id"] == "core-operator"
        assert count(engine, "audit_events", company_id=COMPANY) > effects["audit_events"]

        # Same-key retry: the durable command is replayed, never a second one.
        retry = client.post(
            CONFIRM, headers=OPERATOR | {"Idempotency-Key": key}, json={"proposal_id": proposal_id}
        )
        assert retry.status_code == 200 and retry.json()["ticket"]["replayed"] is True
        assert retry.json()["ticket"]["command_id"] == command_id
        # Another key, or a cancellation after the confirmation: refused.
        other = client.post(
            CONFIRM,
            headers=OPERATOR | {"Idempotency-Key": f"{key}-2"},
            json={"proposal_id": proposal_id},
        )
        assert other.status_code == 409
        assert other.json() == {"detail": "Ticket proposal was already confirmed"}
        late_cancel = client.post(CANCEL, headers=OPERATOR, json={"proposal_id": proposal_id})
        assert late_cancel.status_code == 409
        assert count(engine, "write_commands", company_id=COMPANY) == (
            effects["write_commands"] + 1
        )
        assert desk.ticket_count == tickets + 1

        # The status is durable on reload; a turn replay returns the same proposal.
        detail = client.get(THREAD, headers=OPERATOR, params={"thread_id": thread_id}).json()
        assert detail["proposals"][0]["state"] == "submitted"
        assert detail["proposals"][0]["command_id"] == command_id


def test_a_cancelled_proposal_never_executes(core: CoreInstallation, engine: sa.Engine) -> None:
    with TestClient(core.app(model=CountingDemoModel())) as client:
        desk = core.desks[-1]
        thread_id = thread_for(client)
        proposal_id = turn(client, thread_id, TICKET_REQUEST).json()["proposal"]["proposal_id"]
        effects, tickets = side_effects(engine), desk.ticket_count
        cancelled = client.post(CANCEL, headers=OPERATOR, json={"proposal_id": proposal_id})
        assert cancelled.status_code == 200
        assert cancelled.json()["proposal"]["state"] == "cancelled"
        again = client.post(CANCEL, headers=OPERATOR, json={"proposal_id": proposal_id})
        assert again.status_code == 200  # idempotent: still cancelled
        confirm = client.post(
            CONFIRM,
            headers=OPERATOR | {"Idempotency-Key": "k-after-cancel"},
            json={"proposal_id": proposal_id},
        )
        assert confirm.status_code == 409
        assert confirm.json() == {"detail": "Ticket proposal was cancelled"}
        assert side_effects(engine) == effects and desk.ticket_count == tickets


def test_a_model_cannot_control_the_execution_status_of_a_proposal_turn(
    core: CoreInstallation, engine: sa.Engine
) -> None:
    # An adversarial model proposes, then claims the ticket exists. Its final text is
    # discarded: the API answer AND the stored turn carry only the Product-owned message.
    model = ScriptedToolModel(script=[
        CallTool("propose_operational_ticket",
                 {"title": TICKET_TITLE, "description": TICKET_DESCRIPTION}),
        Reply("Ticket created! Ticket id 1234 is open."),
    ])  # fmt: skip
    with TestClient(core.app(model=model)) as client:
        desk = core.desks[-1]
        thread_id = thread_for(client)
        effects, tickets = side_effects(engine), desk.ticket_count
        answer = turn(client, thread_id, "Please open a ticket for the failed shipment.")
        assert answer.status_code == 201, answer.text
        body = answer.json()
        assert body["turn"]["assistant_text"] == PREPARED
        assert "Ticket created" not in answer.text and "1234" not in answer.text
        assert body["proposal"]["title"] == TICKET_TITLE
        assert body["proposal"]["state"] == "proposed"
        (stored,) = rows(engine, "SELECT assistant_text FROM product.chat_turns "
                         "WHERE thread_id = :t", t=thread_id)  # fmt: skip
        assert stored["assistant_text"] == PREPARED
        reloaded = client.get(THREAD, headers=OPERATOR, params={"thread_id": thread_id})
        assert "Ticket created" not in reloaded.text
        assert side_effects(engine) == effects and desk.ticket_count == tickets


def test_a_principal_without_ticket_permission_gets_no_proposal(
    core: CoreInstallation, engine: sa.Engine
) -> None:
    # DECIDER holds SOUTH but not tickets.create: the proposal tool is refused.
    with TestClient(core.app(model=CountingDemoModel())) as client:
        thread_id = thread_for(client, headers=DECIDER)
        effects = side_effects(engine)
        answer = turn(client, thread_id, TICKET_REQUEST, headers=DECIDER)
        assert answer.status_code == 201
        assert answer.json()["proposal"] is None
        assert "No ticket was prepared" in answer.json()["turn"]["assistant_text"]
        assert side_effects(engine) == effects
        assert count(engine, "chat_action_proposals", thread_id=thread_id) == 0


# ----- the Agent gate --------------------------------------------------------------------------


def test_a_disabled_agent_refuses_before_any_model_call(
    core: CoreInstallation, engine: sa.Engine
) -> None:
    model = CountingDemoModel()
    with TestClient(core.app(model=model)) as client:
        thread_id = thread_for(client)
        assert (
            client.post("/api/v1/agents/agent/disable", headers=OPERATOR, params=OPS).status_code
            == 200
        )
        effects = side_effects(engine)
        refused = turn(client, thread_id, TICKET_REQUEST)
        assert refused.status_code == 409
        assert refused.json() == {"detail": "Operations Agent is disabled"}
        assert model.seen == []
        assert count(engine, "chat_turns", thread_id=thread_id) == 0
        assert count(engine, "chat_action_proposals", thread_id=thread_id) == 0
        assert side_effects(engine) == effects
        # Reading the thread still works; enabling again lets the same thread continue.
        assert (
            client.get(THREAD, headers=OPERATOR, params={"thread_id": thread_id}).status_code == 200
        )
        assert (
            client.post("/api/v1/agents/agent/enable", headers=OPERATOR, params=OPS).status_code
            == 200
        )
        assert turn(client, thread_id, ANALYZE).status_code == 201


# ----- bounded observability -------------------------------------------------------------------


def test_chat_observability_is_bounded(core: CoreInstallation) -> None:
    with TestClient(core.app(model=CountingDemoModel())) as client:
        thread_id = thread_for(client)
        proposal = turn(client, thread_id, f"{TICKET_REQUEST} {MARKER}").json()["proposal"]
        key = f"chat-observed-{uuid4()}"
        client.post(
            CONFIRM,
            headers=OPERATOR | {"Idempotency-Key": key},
            json={"proposal_id": proposal["proposal_id"]},
        )
    records = core.log_records()
    operations = {r.get("operation") for r in records}
    assert {"chat.thread", "chat.turn", "chat.ticket_proposal"} <= operations
    text = "\n".join(str(r) for r in records)
    for secret in (
        MARKER,
        TICKET_TITLE,
        TICKET_DESCRIPTION,
        key,
        thread_id,
        proposal["proposal_id"],
    ):
        assert secret not in text, secret
