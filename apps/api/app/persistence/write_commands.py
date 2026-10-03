"""PostgreSQL implementation of ``WriteCommandStore`` (table ``product.write_commands``).

Claim algorithm, one short transaction, nothing else held open:

    INSERT ... ON CONFLICT (company_id, actor_id, idempotency_key_hash) DO NOTHING
    RETURNING *
      row returned -> NEW (committed IN_PROGRESS when the transaction ends)
      no row       -> SELECT the existing row of that namespace
                      same fingerprint -> REPLAY, different -> CONFLICT

PostgreSQL's unique index makes concurrent claims safe: a racing INSERT waits for the
first transaction and then does nothing, and the following SELECT (a new statement
under READ COMMITTED) sees the committed row.

``complete`` updates by ``command_id`` only while the row is IN_PROGRESS.
``resume_after_approval`` (Task 036) is a compare-and-set from AWAITING_APPROVAL (awaiting
exactly that approval id, of that company and actor) back to IN_PROGRESS: at most one
caller continues a command.
``get_for_actor`` (``WriteCommandReader``) selects by command_id AND company_id AND
actor_id in a single query. Rows are
mapped strictly: an unknown status/reason or inconsistent fields raise
``WriteCommandStoreError`` (fail closed). Only hashes are stored: never the raw
idempotency key or request parameters.
"""

import hmac
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from pydantic import ValidationError
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.commands.errors import WriteCommandStoreError
from app.commands.models import (
    ClaimOutcome,
    ClaimResult,
    CommandStatus,
    WriteCommandClaim,
    WriteCommandOutcome,
    WriteCommandRecord,
)
from app.persistence.database import product_metadata

# Mirrors migration 0001 (checked by ``alembic check`` in the tests); never created here.
write_commands = sa.Table(
    "write_commands",
    product_metadata,
    sa.Column("command_id", sa.Uuid(), nullable=False),
    sa.Column("company_id", sa.Text(), nullable=False),
    sa.Column("actor_id", sa.Text(), nullable=False),
    sa.Column("store_id", sa.Text(), nullable=True),
    sa.Column("action_name", sa.String(128), nullable=False),
    sa.Column("idempotency_key_hash", sa.CHAR(64), nullable=False),
    sa.Column("request_fingerprint", sa.CHAR(64), nullable=False),
    sa.Column("status", sa.String(32), nullable=False),
    sa.Column("reason", sa.String(64), nullable=True),
    sa.Column("action_run_id", sa.Uuid(), nullable=True),
    sa.Column("execution_reference_id", sa.String(128), nullable=True),
    sa.Column("audit_complete", sa.Boolean(), nullable=True),
    # Task 036 (migration 0007): the human-approval request this command awaits or ran
    # under. Authorization metadata only: never part of the request fingerprint.
    sa.Column("approval_id", sa.Uuid(), nullable=True),
    sa.Column(
        "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    ),
    sa.Column(
        "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    ),
    sa.PrimaryKeyConstraint("command_id", name="pk_write_commands"),
    sa.UniqueConstraint(
        "company_id", "actor_id", "idempotency_key_hash", name="uq_write_commands_idempotency"
    ),
)

_c = write_commands.c
# Columns an application-facing record may carry: never the hashes.
_RECORD_COLUMNS = (
    _c.command_id, _c.company_id, _c.actor_id, _c.store_id, _c.action_name, _c.status,
    _c.reason, _c.action_run_id, _c.execution_reference_id, _c.audit_complete,
    _c.created_at, _c.updated_at, _c.approval_id,
)  # fmt: skip


def _record(row: sa.RowMapping) -> WriteCommandRecord:
    try:
        return WriteCommandRecord.model_validate(
            {col.name: row[col.name] for col in _RECORD_COLUMNS}
        )
    except (ValidationError, KeyError, TypeError, ValueError):
        raise WriteCommandStoreError() from None


class PostgresWriteCommandStore:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = session_factory

    async def claim(self, claim: WriteCommandClaim) -> ClaimResult:
        values: dict[str, Any] = {
            "command_id": claim.command_id,
            "company_id": claim.company_id,
            "actor_id": claim.actor_id,
            "store_id": claim.store_id,
            "action_name": claim.action_name,
            "idempotency_key_hash": claim.idempotency_key_hash,
            "request_fingerprint": claim.request_fingerprint,
            "status": CommandStatus.IN_PROGRESS.value,
        }
        inserted = (
            insert(write_commands)
            .values(values)
            .on_conflict_do_nothing(constraint="uq_write_commands_idempotency")
            .returning(*_RECORD_COLUMNS)
        )
        existing = sa.select(*_RECORD_COLUMNS, _c.request_fingerprint).where(
            _c.company_id == claim.company_id,
            _c.actor_id == claim.actor_id,
            _c.idempotency_key_hash == claim.idempotency_key_hash,
        )
        async with self._sessions() as session, session.begin():
            row = (await session.execute(inserted)).mappings().first()
            if row is not None:
                new = ClaimResult(outcome=ClaimOutcome.NEW, record=_record(row))
            else:
                row = (await session.execute(existing)).mappings().first()
                new = None
        # The transaction has committed here: a NEW claim is durable before returning.
        if new is not None:
            return new
        if row is None:
            raise WriteCommandStoreError()  # a conflicting row vanished: fail closed
        stored_fingerprint = row["request_fingerprint"]
        if not isinstance(stored_fingerprint, str) or not hmac.compare_digest(
            stored_fingerprint, claim.request_fingerprint
        ):
            return ClaimResult(outcome=ClaimOutcome.CONFLICT)
        return ClaimResult(outcome=ClaimOutcome.REPLAY, record=_record(row))

    async def get(self, command_id: UUID) -> WriteCommandRecord | None:
        query = sa.select(*_RECORD_COLUMNS).where(_c.command_id == command_id)
        async with self._sessions() as session:
            row = (await session.execute(query)).mappings().first()
        return None if row is None else _record(row)

    async def get_for_actor(
        self, command_id: UUID, company_id: str, actor_id: str
    ) -> WriteCommandRecord | None:
        """``WriteCommandReader``: one query scoped by command, company AND actor, so
        another principal's row is never read. Read-only; hashes are never selected."""
        query = sa.select(*_RECORD_COLUMNS).where(
            _c.command_id == command_id,
            _c.company_id == company_id,
            _c.actor_id == actor_id,
        )
        async with self._sessions() as session:
            row = (await session.execute(query)).mappings().first()
        return None if row is None else _record(row)

    async def complete(self, command_id: UUID, outcome: WriteCommandOutcome) -> WriteCommandRecord:
        if not isinstance(outcome, WriteCommandOutcome):
            raise WriteCommandStoreError()
        update = (
            sa.update(write_commands)
            .where(
                _c.command_id == command_id,
                _c.status == CommandStatus.IN_PROGRESS.value,
            )
            .values(
                status=outcome.status.value,
                reason=outcome.reason.value,
                action_run_id=outcome.action_run_id,
                execution_reference_id=outcome.execution_reference_id,
                audit_complete=outcome.audit_complete,
                approval_id=outcome.approval_id,
                updated_at=sa.func.now(),
            )
            .returning(*_RECORD_COLUMNS)
        )
        async with self._sessions() as session, session.begin():
            row = (await session.execute(update)).mappings().first()
        if row is None:
            raise WriteCommandStoreError()  # unknown command or no longer IN_PROGRESS
        return _record(row)

    async def resume_after_approval(
        self, command_id: UUID, company_id: str, actor_id: str, approval_id: UUID
    ) -> WriteCommandRecord | None:
        update = (
            sa.update(write_commands)
            .where(
                _c.command_id == command_id,
                _c.company_id == company_id,
                _c.actor_id == actor_id,
                _c.status == CommandStatus.AWAITING_APPROVAL.value,
                _c.approval_id == approval_id,
            )
            .values(
                status=CommandStatus.IN_PROGRESS.value,
                reason=None,
                action_run_id=None,
                execution_reference_id=None,
                audit_complete=None,
                updated_at=sa.func.now(),
            )
            .returning(*_RECORD_COLUMNS)
        )
        async with self._sessions() as session, session.begin():
            row = (await session.execute(update)).mappings().first()
        return None if row is None else _record(row)
