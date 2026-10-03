"""PostgreSQL implementation of ``WorkflowRunRepository`` (tables ``product.workflow_runs``,
``product.workflow_step_runs`` and ``product.workflow_events``, owned by migration 0005;
never created here).

Execution CONTROL state only. One short transaction per call (never held while Step code
runs). Every query is scoped by the trusted company id in the query itself. Claims are
compare-and-set updates: ``claim`` succeeds only on an active run whose claim is free or
expired; ``advance`` applies a change only while the run still carries the caller's token
and expected status, locking the run row first so events are numbered serially. Rows are
rebuilt strictly through the domain records (invalid stored data fails closed). Every
failure is a fixed-message ``WorkflowRepositoryError`` (never chained).
"""

from datetime import datetime
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from pydantic import ValidationError
from sqlalchemy import exc as sa_exc
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession, async_sessionmaker

from app.persistence.database import product_metadata
from app.workflow_management.contracts import (
    Claim,
    RunChange,
    WorkflowClaimConflictError,
    WorkflowRepositoryError,
)
from app.workflow_management.records import (
    NewWorkflowEvent,
    StepAttemptRecord,
    WorkflowEventRecord,
    WorkflowRunRecord,
)
from app.workflow_management.state import (
    ACTIVE_RUN_STATUSES,
    StepAttemptStatus,
    WorkflowEventType,
    WorkflowRunStatus,
)

# Mirror migration 0005 (``alembic check`` and the vocabulary test keep them in step;
# CHECK constraints and the append-only trigger live in the migration).
workflow_runs = sa.Table(
    "workflow_runs",
    product_metadata,
    sa.Column("run_id", sa.Uuid(), nullable=False),
    sa.Column("workflow_id", sa.String(128), nullable=False),
    sa.Column("workflow_version", sa.Integer(), nullable=False),
    sa.Column("request_id", sa.Uuid(), nullable=False),
    sa.Column("company_id", sa.Text(), nullable=False),
    sa.Column("actor_id", sa.Text(), nullable=False),
    sa.Column("actor_type", sa.String(32), nullable=False),
    sa.Column("channel", sa.String(32), nullable=False),
    sa.Column("store_id", sa.Text(), nullable=True),
    sa.Column("status", sa.String(32), nullable=False),
    sa.Column("current_step_id", sa.String(64), nullable=True),
    sa.Column("failure_code", sa.String(64), nullable=True),
    sa.Column("input_state", JSONB(), nullable=False),
    sa.Column("input_fingerprint", sa.String(64), nullable=False),
    sa.Column("lease_owner", sa.Uuid(), nullable=True),
    sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    sa.PrimaryKeyConstraint("run_id", name="pk_workflow_runs"),
    sa.Index("ix_workflow_runs_company_id_created_at", "company_id", "created_at", "run_id"),
)
workflow_step_runs = sa.Table(
    "workflow_step_runs",
    product_metadata,
    sa.Column("run_id", sa.Uuid(), nullable=False),
    sa.Column("company_id", sa.Text(), nullable=False),
    sa.Column("step_id", sa.String(64), nullable=False),
    sa.Column("attempt", sa.Integer(), nullable=False),
    sa.Column("handler_id", sa.String(128), nullable=False),
    sa.Column("status", sa.String(32), nullable=False),
    sa.Column("failure_code", sa.String(64), nullable=True),
    sa.Column("verification_code", sa.String(32), nullable=True),
    sa.Column("checkpoint", JSONB(none_as_null=True), nullable=True),  # None is SQL NULL
    sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    # Task 036 (migration 0007): the human-approval request a governed write attempt
    # awaited or executed under. Correlation only: never action parameters.
    sa.Column("approval_id", sa.Uuid(), nullable=True),
    sa.PrimaryKeyConstraint("run_id", "step_id", "attempt", name="pk_workflow_step_runs"),
    sa.ForeignKeyConstraint(["run_id"], ["product.workflow_runs.run_id"],
                            name="fk_workflow_step_runs_run_id"),
)  # fmt: skip
workflow_events = sa.Table(
    "workflow_events",
    product_metadata,
    sa.Column("run_id", sa.Uuid(), nullable=False),
    sa.Column("sequence", sa.Integer(), nullable=False),
    sa.Column("company_id", sa.Text(), nullable=False),
    sa.Column("event_type", sa.String(32), nullable=False),
    sa.Column("step_id", sa.String(64), nullable=True),
    sa.Column("attempt", sa.Integer(), nullable=True),
    sa.Column("status", sa.String(32), nullable=True),
    sa.Column("failure_code", sa.String(64), nullable=True),
    sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("recorded_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
              nullable=False),
    sa.PrimaryKeyConstraint("run_id", "sequence", name="pk_workflow_events"),
    sa.ForeignKeyConstraint(["run_id"], ["product.workflow_runs.run_id"],
                            name="fk_workflow_events_run_id"),
)  # fmt: skip

_r = workflow_runs.c
_s = workflow_step_runs.c
_e = workflow_events.c
_ACTIVE = tuple(s.value for s in ACTIVE_RUN_STATUSES)
_DB_ERRORS = (sa_exc.SQLAlchemyError, OSError)


def _rebuild[T](model: type[T], row: Any) -> T:
    try:
        return model.model_validate(dict(row))  # type: ignore[attr-defined]
    except (ValidationError, TypeError, ValueError):
        raise WorkflowRepositoryError() from None


def _event_row(run_id: UUID, company_id: str, sequence: int, event: NewWorkflowEvent) -> dict:
    return {"run_id": run_id, "company_id": company_id, "sequence": sequence,
            **event.model_dump(mode="python")}  # fmt: skip


class PostgresWorkflowRunRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = session_factory

    async def create_run(self, run: WorkflowRunRecord, event: NewWorkflowEvent) -> None:
        try:
            async with self._sessions() as session, session.begin():
                await session.execute(sa.insert(workflow_runs).values(**run.model_dump()))
                await session.execute(sa.insert(workflow_events).values(
                    **_event_row(run.run_id, run.company_id, 1, event)))  # fmt: skip
        except _DB_ERRORS:
            raise WorkflowRepositoryError() from None

    async def claim(
        self, company_id: str, run_id: UUID, token: UUID, now: datetime, lease_expires_at: datetime
    ) -> WorkflowRunRecord:
        statement = (
            sa.update(workflow_runs)
            .where(_r.run_id == run_id, _r.company_id == company_id, _r.status.in_(_ACTIVE),
                   sa.or_(_r.lease_owner.is_(None), _r.lease_expires_at <= now))
            .values(lease_owner=token, lease_expires_at=lease_expires_at)
            .returning(*workflow_runs.c)
        )  # fmt: skip
        exists = None
        try:
            async with self._sessions() as session, session.begin():
                row = (await session.execute(statement)).mappings().first()
                if row is None:
                    exists = (await session.execute(sa.select(_r.run_id).where(
                        _r.run_id == run_id, _r.company_id == company_id))).first()  # fmt: skip
        except _DB_ERRORS:
            raise WorkflowRepositoryError() from None
        if row is None:
            if exists is None:
                raise LookupError("workflow run not found")
            raise WorkflowClaimConflictError()
        return _rebuild(WorkflowRunRecord, row)

    async def advance(self, claim: Claim, change: RunChange) -> WorkflowRunRecord:
        values: dict[str, Any] = {
            "status": change.status.value, "current_step_id": change.current_step_id,
            "failure_code": None if change.failure_code is None else change.failure_code.value,
            "updated_at": change.updated_at, "completed_at": change.completed_at,
            "lease_expires_at": change.lease_expires_at,
            "lease_owner": claim.token if change.lease_expires_at is not None else None,
        }  # fmt: skip
        statement = (
            sa.update(workflow_runs)
            .where(_r.run_id == claim.run_id, _r.company_id == claim.company_id,
                   _r.lease_owner == claim.token, _r.status == change.expected_status.value)
            .values(**values)
            .returning(*workflow_runs.c)
        )  # fmt: skip
        try:
            async with self._sessions() as session, session.begin():
                # The CAS update locks the run row: concurrent writers of this run serialize.
                row = (await session.execute(statement)).mappings().first()
                if row is None:
                    conflict = True
                else:
                    conflict = False
                    connection = await session.connection()
                    await self._apply_attempts(connection, claim, change)
                    await self._append_events(connection, claim, change.events)
        except _DB_ERRORS:
            raise WorkflowRepositoryError() from None
        if conflict:
            raise WorkflowClaimConflictError()
        return _rebuild(WorkflowRunRecord, row)

    async def reopen_for_approval(
        self, company_id: str, run_id: UUID, step_id: str, approval_id: UUID, token: UUID,
        now: datetime, lease_expires_at: datetime,
    ) -> WorkflowRunRecord:  # fmt: skip
        """Task 036: the explicit ``awaiting_approval -> running`` continuation (CAS)."""
        latest = (sa.select(sa.func.max(_s.attempt))
                  .where(_s.run_id == run_id, _s.company_id == company_id, _s.step_id == step_id)
                  .scalar_subquery())  # fmt: skip
        awaiting = sa.exists().where(
            _s.run_id == run_id, _s.company_id == company_id, _s.step_id == step_id,
            _s.attempt == latest, _s.status == StepAttemptStatus.AWAITING_APPROVAL.value,
            _s.approval_id == approval_id,
        )  # fmt: skip
        statement = (
            sa.update(workflow_runs)
            .where(_r.run_id == run_id, _r.company_id == company_id,
                   _r.status == WorkflowRunStatus.AWAITING_APPROVAL.value,
                   _r.current_step_id == step_id, awaiting)
            .values(status=WorkflowRunStatus.RUNNING.value, failure_code=None, completed_at=None,
                    lease_owner=token, lease_expires_at=lease_expires_at, updated_at=now)
            .returning(*workflow_runs.c)
        )  # fmt: skip
        resumed = NewWorkflowEvent(event_type=WorkflowEventType.WORKFLOW_APPROVAL_RESUMED,
                                   step_id=step_id, status=WorkflowRunStatus.RUNNING,
                                   occurred_at=now)  # fmt: skip
        exists = None
        try:
            async with self._sessions() as session, session.begin():
                row = (await session.execute(statement)).mappings().first()
                if row is None:
                    exists = (await session.execute(sa.select(_r.run_id).where(
                        _r.run_id == run_id, _r.company_id == company_id))).first()  # fmt: skip
                else:
                    connection = await session.connection()
                    claim = Claim(run_id=run_id, company_id=company_id, token=token)
                    await self._append_events(connection, claim, (resumed,))
        except _DB_ERRORS:
            raise WorkflowRepositoryError() from None
        if row is None:
            if exists is None:
                raise LookupError("workflow run not found")
            raise WorkflowClaimConflictError()
        return _rebuild(WorkflowRunRecord, row)

    @staticmethod
    async def _apply_attempts(connection: AsyncConnection, claim: Claim, change: RunChange) -> None:
        if change.start_attempt is not None:
            attempt = change.start_attempt
            if attempt.run_id != claim.run_id or attempt.company_id != claim.company_id:
                raise sa_exc.InvalidRequestError("attempt outside the claimed run")
            await connection.execute(sa.insert(workflow_step_runs).values(**attempt.model_dump()))
        finish = change.finish_attempt
        if finish is not None:
            extra = {} if finish.approval_id is None else {"approval_id": finish.approval_id}
            result = await connection.execute(
                sa.update(workflow_step_runs)
                .where(_s.run_id == claim.run_id, _s.company_id == claim.company_id,
                       _s.step_id == finish.step_id, _s.attempt == finish.attempt,
                       _s.status == StepAttemptStatus.RUNNING.value)
                .values(status=finish.status.value,
                        failure_code=None if finish.failure_code is None
                        else finish.failure_code.value,
                        verification_code=None if finish.verification_code is None
                        else finish.verification_code.value,
                        checkpoint=finish.checkpoint, completed_at=finish.completed_at,
                        **extra)
            )  # fmt: skip
            if getattr(result, "rowcount", 0) != 1:
                # Never "finish" an attempt that is not running: roll the change back.
                raise sa_exc.InvalidRequestError("attempt is not running")

    @staticmethod
    async def _append_events(
        connection: AsyncConnection, claim: Claim, events: tuple[NewWorkflowEvent, ...]
    ) -> None:
        if not events:
            return
        last = (await connection.execute(
            sa.select(sa.func.coalesce(sa.func.max(_e.sequence), 0))
            .where(_e.run_id == claim.run_id, _e.company_id == claim.company_id)
        )).scalar_one()  # fmt: skip
        await connection.execute(sa.insert(workflow_events), [
            _event_row(claim.run_id, claim.company_id, last + index, event)
            for index, event in enumerate(events, start=1)
        ])  # fmt: skip

    # ----- reads (company scoped in the query) ------------------------------------------------

    async def get_run(self, company_id: str, run_id: UUID) -> WorkflowRunRecord | None:
        query = sa.select(workflow_runs).where(_r.run_id == run_id, _r.company_id == company_id)
        try:
            async with self._sessions() as session:
                row = (await session.execute(query)).mappings().first()
        except _DB_ERRORS:
            raise WorkflowRepositoryError() from None
        return None if row is None else _rebuild(WorkflowRunRecord, row)

    async def list_runs(
        self, company_id: str, limit: int
    ) -> tuple[tuple[WorkflowRunRecord, int], ...]:
        attempts = (sa.select(sa.func.count()).select_from(workflow_step_runs)
                    .where(_s.run_id == _r.run_id, _s.company_id == company_id)
                    .scalar_subquery())  # fmt: skip
        query = (sa.select(workflow_runs, attempts.label("attempt_count"))
                 .where(_r.company_id == company_id)
                 .order_by(_r.created_at.desc(), _r.run_id.desc()).limit(limit))  # fmt: skip
        try:
            async with self._sessions() as session:
                rows = (await session.execute(query)).mappings().all()
        except _DB_ERRORS:
            raise WorkflowRepositoryError() from None
        result = []
        for row in rows:
            data = dict(row)
            count = data.pop("attempt_count")
            result.append((_rebuild(WorkflowRunRecord, data), int(count)))
        return tuple(result)

    async def list_attempts(self, company_id: str, run_id: UUID) -> tuple[StepAttemptRecord, ...]:
        query = (sa.select(workflow_step_runs)
                 .where(_s.run_id == run_id, _s.company_id == company_id)
                 .order_by(_s.started_at, _s.step_id, _s.attempt))  # fmt: skip
        try:
            async with self._sessions() as session:
                rows = (await session.execute(query)).mappings().all()
        except _DB_ERRORS:
            raise WorkflowRepositoryError() from None
        return tuple(_rebuild(StepAttemptRecord, row) for row in rows)

    async def list_events(self, company_id: str, run_id: UUID) -> tuple[WorkflowEventRecord, ...]:
        query = (sa.select(*(c for c in workflow_events.c if c.name != "recorded_at"))
                 .where(_e.run_id == run_id, _e.company_id == company_id)
                 .order_by(_e.sequence))  # fmt: skip
        try:
            async with self._sessions() as session:
                rows = (await session.execute(query)).mappings().all()
        except _DB_ERRORS:
            raise WorkflowRepositoryError() from None
        return tuple(_rebuild(WorkflowEventRecord, row) for row in rows)
