"""PostgresWriteCommandStore against real PostgreSQL: claim semantics, concurrency,
terminal updates and fail-closed row mapping."""

import asyncio
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa

from app.commands import (
    ClaimOutcome,
    CommandReason,
    CommandStatus,
    WriteCommandClaim,
    WriteCommandOutcome,
    WriteCommandStore,
    WriteCommandStoreError,
    hash_idempotency_key,
    request_fingerprint,
)
from tests.integration.product_db import command_rows, product_store

pytestmark = pytest.mark.integration
S, R, C = CommandStatus, CommandReason, ClaimOutcome


def claim(company: str, **overrides) -> WriteCommandClaim:
    data = {
        "command_id": uuid4(),
        "company_id": company,
        "actor_id": "actor-1",
        "store_id": "store-a",
        "action_name": "operations.ticket.create",
        "idempotency_key_hash": hash_idempotency_key("key-1"),
        "request_fingerprint": request_fingerprint(
            action_name="operations.ticket.create", company_id=company, store_id="store-a",
            parameters={"title": "t"},
        ),
    }  # fmt: skip
    return WriteCommandClaim(**(data | overrides))


@pytest.fixture
def company() -> str:
    return f"company-{uuid4()}"  # isolates every test's rows


VERIFIED = WriteCommandOutcome(
    status=S.VERIFIED, reason=R.VERIFIED, action_run_id=UUID(int=7),
    execution_reference_id="0b0b0b0b-0000-4000-8000-000000000001", audit_complete=True,
)  # fmt: skip


def test_postgres_store_satisfies_the_protocol() -> None:
    from app.persistence import PostgresWriteCommandStore

    assert isinstance(PostgresWriteCommandStore(None), WriteCommandStore)  # type: ignore[arg-type]


def test_claim_new_replay_conflict_and_namespaces(migrated, engine, company) -> None:
    async def scenario():
        async with product_store(migrated) as store:
            first = claim(company)
            new = await store.claim(first)
            assert new.outcome is C.NEW
            assert new.record.command_id == first.command_id
            assert (new.record.status, new.record.reason) == (S.IN_PROGRESS, None)
            assert new.record.created_at.tzinfo is not None

            replay = await store.claim(claim(company))  # new command id, same request
            assert replay.outcome is C.REPLAY and replay.record == new.record

            other = claim(company, request_fingerprint="f" * 64)
            conflict = await store.claim(other)
            assert conflict.outcome is C.CONFLICT and conflict.record is None

            for separate in (
                claim(company, idempotency_key_hash=hash_idempotency_key("key-2")),
                claim(company, actor_id="actor-2"),
                claim(f"{company}-other"),
            ):
                assert (await store.claim(separate)).outcome is C.NEW

    asyncio.run(scenario())
    rows = command_rows(engine, company)
    assert len(rows) == 3 and {r["status"] for r in rows} == {"in_progress"}


def test_claim_is_committed_before_it_returns(migrated, engine, company) -> None:
    async def scenario():
        async with product_store(migrated) as store:
            await store.claim(claim(company))
            # Visible to a completely separate (sync) connection immediately.
            return command_rows(engine, company)

    (row,) = asyncio.run(scenario())
    assert row["status"] == "in_progress"


def test_complete_updates_only_in_progress_commands(migrated, company) -> None:
    async def scenario():
        async with product_store(migrated) as store:
            new = await store.claim(claim(company))
            done = await store.complete(new.record.command_id, VERIFIED)
            assert (done.status, done.reason, done.action_run_id) == (
                S.VERIFIED, R.VERIFIED, UUID(int=7),
            )  # fmt: skip
            assert done.execution_reference_id == VERIFIED.execution_reference_id
            assert done.updated_at >= done.created_at
            assert await store.get(new.record.command_id) == done
            # Terminal commands are never overwritten.
            other = WriteCommandOutcome(status=S.FAILED, reason=R.INPUT_INVALID)
            with pytest.raises(WriteCommandStoreError):
                await store.complete(new.record.command_id, other)
            with pytest.raises(WriteCommandStoreError):
                await store.complete(uuid4(), other)
            assert await store.get(uuid4()) is None
            # A replay after completion returns the terminal record.
            replay = await store.claim(claim(company))
            assert replay.outcome is C.REPLAY and replay.record == done

    asyncio.run(scenario())


def test_concurrent_claims_create_exactly_one_row(migrated, engine, company) -> None:
    attempts = 12

    async def one(i: int):
        # Each attempt has its own engine and connection: separate 'processes'.
        async with product_store(migrated) as store:
            return await store.claim(claim(company))

    async def scenario():
        return await asyncio.gather(*(one(i) for i in range(attempts)))

    results = asyncio.run(scenario())
    outcomes = [r.outcome for r in results]
    assert outcomes.count(C.NEW) == 1
    assert outcomes.count(C.REPLAY) == attempts - 1
    (row,) = command_rows(engine, company)
    assert {r.record.command_id for r in results} == {row["command_id"]}


def test_concurrent_claims_on_one_shared_pool(migrated, engine, company) -> None:
    async def scenario():
        async with product_store(migrated, pool_size=10, max_overflow=0) as store:
            return await asyncio.gather(*(store.claim(claim(company)) for _ in range(10)))

    outcomes = [r.outcome for r in asyncio.run(scenario())]
    assert outcomes.count(C.NEW) == 1 and outcomes.count(C.REPLAY) == 9
    assert len(command_rows(engine, company)) == 1


@pytest.mark.parametrize(
    "corruption",
    [
        # Each passes the table's CHECK constraints but is not a state the product
        # can map safely: the store must fail closed, never report success.
        "status = 'failed', reason = 'mystery_reason'",
        "status = 'failed', reason = 'verified'",
        "status = 'requires_human', reason = 'verified'",
        "status = 'verified', reason = 'audit_incomplete', action_run_id = gen_random_uuid(),"
        " audit_complete = true",
        "action_run_id = gen_random_uuid()",
        "status = 'failed', reason = 'input_invalid', execution_reference_id = 'not a ref'",
    ],
)
def test_unknown_or_corrupt_rows_fail_closed(migrated, engine, company, corruption) -> None:
    async def claim_one():
        async with product_store(migrated) as store:
            return (await store.claim(claim(company))).record.command_id

    command_id = asyncio.run(claim_one())
    with engine.begin() as connection:  # out-of-band corruption
        connection.execute(
            sa.text(f"UPDATE product.write_commands SET {corruption} WHERE command_id = :i"),  # noqa: S608 - fixed test literals
            {"i": command_id},
        )

    async def read():
        async with product_store(migrated) as store:
            with pytest.raises(WriteCommandStoreError):
                await store.get(command_id)
            with pytest.raises(WriteCommandStoreError):
                await store.claim(claim(company))  # a replay of a corrupt row

    asyncio.run(read())


def test_database_constraints_reject_invalid_state(migrated, engine, company) -> None:
    base = {"c": company, "k": "a" * 64, "f": "b" * 64}
    bad = [
        "'success', NULL",  # unknown status
        "'in_progress', 'verified'",  # in progress with an outcome
        "'failed', NULL",  # terminal without a reason
        "'verified', 'verified'",  # verified without run id / full audit
    ]
    for values in bad:
        with pytest.raises(sa.exc.IntegrityError), engine.begin() as connection:
            columns = (
                "command_id, company_id, actor_id, action_name, idempotency_key_hash,"
                " request_fingerprint, status, reason"
            )
            statement = f"INSERT INTO product.write_commands ({columns}) VALUES (gen_random_uuid(), :c, 'a', 'x.y', :k, :f, {values})"  # noqa: E501, S608 - fixed test literals
            connection.execute(sa.text(statement), base)
    with pytest.raises(sa.exc.IntegrityError), engine.begin() as connection:
        connection.execute(
            sa.text(
                "INSERT INTO product.write_commands (command_id, company_id, actor_id,"
                " action_name, idempotency_key_hash, request_fingerprint, status)"
                " VALUES (gen_random_uuid(), :c, 'a', 'x.y', 'raw-key', :f, 'in_progress')"
            ),
            base,
        )
    assert command_rows(engine, company) == []
