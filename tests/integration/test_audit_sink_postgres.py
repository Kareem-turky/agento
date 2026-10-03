"""PostgresAuditSink against real PostgreSQL: commit-before-return, exact roundtrip,
append-only duplicates, concurrency and fail-closed errors."""

import asyncio
from datetime import UTC, datetime
from uuid import uuid4

import pytest
import sqlalchemy as sa

from app.execution import ActionRunReason, ActionRunStatus, AuditEvent, AuditEventType
from app.governance import PolicyOutcome, PolicyReason
from app.persistence import AuditPersistenceError, PostgresAuditSink, create_session_factory
from app.persistence.database import create_product_engine
from tests.integration.product_db import audit_rows, audit_sink

pytestmark = pytest.mark.integration
T0 = datetime(2026, 3, 1, 12, 0, 0, 123456, tzinfo=UTC)


def full_event(**overrides) -> AuditEvent:
    data = {
        "event_id": uuid4(), "run_id": uuid4(), "request_id": uuid4(), "occurred_at": T0,
        "event_type": AuditEventType.VERIFIED, "action_name": "operations.ticket.create",
        "actor_id": "actor-1", "actor_type": "api_client", "company_id": "company-1",
        "store_id": "store-a", "channel": "api", "policy_outcome": PolicyOutcome.ALLOW,
        "policy_reason": PolicyReason.LOW_RISK_WRITE_ALLOWED,
        "run_status": ActionRunStatus.VERIFIED, "run_reason": ActionRunReason.VERIFIED,
        "execution_reference_id": "0b0b0b0b-0000-4000-8000-000000000001",
        "verification_code": "ticket_present",
    }  # fmt: skip
    return AuditEvent(**(data | overrides))


def minimal_event(**overrides) -> AuditEvent:
    data = {
        "event_id": uuid4(), "run_id": uuid4(), "request_id": uuid4(), "occurred_at": T0,
        "event_type": AuditEventType.REQUESTED, "action_name": "unknown.action",
        "actor_id": None, "actor_type": None, "company_id": "company-1", "store_id": None,
        "channel": "system",
    }  # fmt: skip
    return AuditEvent(**(data | overrides))


def test_full_event_is_committed_before_record_returns_and_roundtrips(migrated, engine) -> None:
    event = full_event()

    async def scenario():
        async with audit_sink(migrated) as sink:
            await sink.record(event)
            # Still inside the sink's lifetime: a SEPARATE (sync) connection sees it now.
            return audit_rows(engine, event_id=event.event_id)

    (row,) = asyncio.run(scenario())
    recorded_at = row.pop("recorded_at")
    assert recorded_at.tzinfo is not None
    assert row == {
        "event_id": event.event_id, "run_id": event.run_id, "request_id": event.request_id,
        "occurred_at": T0, "event_type": "verified", "action_name": "operations.ticket.create",
        "actor_id": "actor-1", "actor_type": "api_client", "company_id": "company-1",
        "store_id": "store-a", "channel": "api", "policy_outcome": "allow",
        "policy_reason": "low_risk_write_allowed", "run_status": "verified",
        "run_reason": "verified",
        "execution_reference_id": "0b0b0b0b-0000-4000-8000-000000000001",
        "verification_code": "ticket_present",
        "approval_id": None,  # Task 036: approval correlation (none for a LOW_RISK write)
    }  # fmt: skip
    # The row maps back to the exact event.
    assert AuditEvent.model_validate(row) == event


def test_minimal_event_stores_nulls_not_invented_values(migrated, engine) -> None:
    event = minimal_event()

    async def scenario():
        async with audit_sink(migrated) as sink:
            await sink.record(event)

    asyncio.run(scenario())
    (row,) = audit_rows(engine, event_id=event.event_id)
    for column in ("actor_id", "actor_type", "store_id", "policy_outcome", "policy_reason",
                   "run_status", "run_reason", "execution_reference_id",
                   "verification_code"):  # fmt: skip
        assert row[column] is None, column
    assert row["recorded_at"] is not None
    row.pop("recorded_at")
    assert AuditEvent.model_validate(row) == event


def test_duplicate_event_id_fails_safely_and_keeps_the_original(migrated, engine) -> None:
    event = full_event()
    altered = event.model_copy(update={"verification_code": "tampered", "actor_id": "evil"})

    async def scenario():
        async with audit_sink(migrated) as sink:
            await sink.record(event)
            with pytest.raises(AuditPersistenceError) as first:
                await sink.record(event)
            with pytest.raises(AuditPersistenceError) as second:
                await sink.record(altered)
            return first.value, second.value

    errors = asyncio.run(scenario())
    for error in errors:
        assert str(error) == "audit event could not be persisted"
        assert error.__cause__ is None and error.__suppress_context__
        for leak in ("pk_audit_events", "duplicate", "UniqueViolation", "INSERT", "platform"):
            assert leak not in str(error) + repr(error)
    (row,) = audit_rows(engine, event_id=event.event_id)
    assert (row["verification_code"], row["actor_id"]) == ("ticket_present", "actor-1")


def test_concurrent_distinct_events_are_all_persisted_once(migrated, engine) -> None:
    run_id = uuid4()
    events = [full_event(run_id=run_id, event_type=t) for t in AuditEventType] * 1
    events += [minimal_event(run_id=run_id) for _ in range(12)]

    async def one(event):
        async with audit_sink(migrated) as sink:  # a separate engine/connection each
            await sink.record(event)

    async def shared():
        async with audit_sink(migrated, pool_size=10, max_overflow=0) as sink:
            extra = [minimal_event(run_id=run_id) for _ in range(10)]
            await asyncio.gather(*(sink.record(e) for e in extra))
            return extra

    async def scenario():
        await asyncio.gather(*(one(e) for e in events))
        return await shared()

    extra = asyncio.run(scenario())
    rows = audit_rows(engine, run_id=run_id)
    expected = {e.event_id for e in events + extra}
    assert len(rows) == len(expected) and {r["event_id"] for r in rows} == expected


@pytest.mark.parametrize(
    "bad", [None, {"event_id": "x"}, "requested", object(), 42, [minimal_event()]]
)
def test_non_events_are_rejected_before_any_insert(migrated, engine, bad) -> None:
    async def scenario():
        async with audit_sink(migrated) as sink:
            with pytest.raises(AuditPersistenceError):
                await sink.record(bad)

    before = len(audit_rows(engine))
    asyncio.run(scenario())
    assert len(audit_rows(engine)) == before


def test_database_failures_are_safe_and_generic() -> None:
    url = "postgresql+psycopg://secret_user:secret_pw@127.0.0.1:1/secret_db"

    async def scenario():
        engine = create_product_engine(url)
        try:
            sink = PostgresAuditSink(create_session_factory(engine))
            with pytest.raises(AuditPersistenceError) as info:
                await sink.record(minimal_event())
            return info.value
        finally:
            await engine.dispose()

    error = asyncio.run(scenario())
    text = str(error) + repr(error)
    for leak in ("secret_user", "secret_pw", "secret_db", "127.0.0.1", "OperationalError",
                 "connection"):  # fmt: skip
        assert leak not in text
    assert error.__cause__ is None and error.__suppress_context__


def test_each_record_is_its_own_committed_transaction(migrated, engine) -> None:
    from sqlalchemy import event as sa_event
    from sqlalchemy.engine import Engine

    statements: list[str] = []

    def capture(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement.strip().split()[0].upper())

    def commits(conn):
        statements.append("COMMIT")

    run_id = uuid4()

    async def scenario():
        async with audit_sink(migrated) as sink:
            sa_event.listen(Engine, "before_cursor_execute", capture)
            sa_event.listen(Engine, "commit", commits)
            try:
                for event_type in (AuditEventType.REQUESTED, AuditEventType.POLICY_DECIDED):
                    await sink.record(full_event(run_id=run_id, event_type=event_type))
            finally:
                sa_event.remove(Engine, "before_cursor_execute", capture)
                sa_event.remove(Engine, "commit", commits)

    asyncio.run(scenario())
    assert [s for s in statements if s in ("INSERT", "COMMIT")] == [
        "INSERT", "COMMIT", "INSERT", "COMMIT",
    ]  # fmt: skip
    assert len(audit_rows(engine, run_id=run_id)) == 2


def test_live_check_constraints_match_migration_and_metadata(migrated, engine) -> None:
    """head migrations (0002 + 0007) == SQLAlchemy metadata == live PostgreSQL constraints."""
    from app.persistence import audit_events
    from tests.persistence.test_audit_schema import (
        EXPECTED_0002,
        head_audit_checks,
        metadata_checks,
        migration,
        migration_checks,
        parse_check,
    )

    with engine.connect() as connection:
        live_rows = connection.execute(
            sa.text(
                "SELECT conname, pg_get_constraintdef(oid) AS definition FROM pg_constraint"
                " WHERE conrelid = 'product.audit_events'::regclass AND contype = 'c'"
            )
        ).all()
    live = {name: parse_check(definition) for name, definition in live_rows}
    assert len(live) == 7
    # 0002 created all seven; Task 036 (0007) replaced two of them with supersets.
    assert migration_checks(migration()) == EXPECTED_0002
    assert live == head_audit_checks()
    assert live == metadata_checks(audit_events)


@pytest.mark.parametrize(
    ("column", "value"),
    [("event_type", "exfiltrated"), ("actor_type", "root"), ("channel", "email"),
     ("policy_outcome", "maybe"), ("policy_reason", "because"), ("run_status", "done"),
     ("run_reason", "whatever")],
)  # fmt: skip
def test_live_checks_reject_values_outside_the_vocabulary(migrated, engine, column, value) -> None:
    row = {
        "event_id": uuid4(), "run_id": uuid4(), "request_id": uuid4(), "occurred_at": T0,
        "event_type": "requested", "action_name": "a", "company_id": "c", "channel": "api",
    } | {column: value}  # fmt: skip
    with engine.connect() as connection, pytest.raises(sa.exc.IntegrityError) as info:
        connection.execute(
            sa.text(
                f"INSERT INTO product.audit_events ({', '.join(row)})"  # noqa: S608 - test columns
                f" VALUES ({', '.join(':' + k for k in row)})"
            ),
            row,
        )
    assert f"ck_audit_events_{column}" in str(info.value)
    assert audit_rows(engine, event_id=row["event_id"]) == []
