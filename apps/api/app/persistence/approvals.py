"""PostgreSQL implementation of ``ApprovalRepository`` (Task 036).

Tables ``product.approval_requests`` and ``product.approval_events`` (owned by migration
0007; never created here). One short transaction per call; every query is scoped by the
trusted company id IN SQL (another company's request is indistinguishable from a
missing one).

Every state change is a compare-and-set UPDATE on the current status (which row-locks
the request until commit) followed, in the SAME transaction, by exactly one append-only
event whose sequence is the next number of that request. Concurrent deciders and
concurrent consumers therefore serialize on the row: exactly one transition wins and
the losers change nothing. Rows are rebuilt strictly; malformed data fails closed.
Only the subject fingerprint and the trusted safe summary are stored, never raw action
parameters. Every failure is a fixed-message ``ApprovalRepositoryError``.
"""

from collections.abc import Mapping
from datetime import datetime
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from pydantic import ValidationError
from sqlalchemy import exc as sa_exc
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.approval_management.errors import (
    ApprovalRepositoryError,
    ApprovalTransitionConflictError,
)
from app.approval_management.models import ApprovalEvent, ApprovalRequest
from app.approval_management.state import (
    EVENT_FOR_STATUS,
    HUMAN_DECISIONS,
    ApprovalEventType,
    ApprovalStatus,
)
from app.execution.approvals import (
    ApprovalClaimStatus,
    ApprovalOutcome,
    ApprovalSourceRef,
    ApprovalSummary,
)
from app.persistence.database import product_metadata

# Mirror migration 0007 (``alembic check`` keeps them in step).
approval_requests = sa.Table(
    "approval_requests",
    product_metadata,
    sa.Column("approval_id", sa.Uuid(), nullable=False),
    sa.Column("company_id", sa.Text(), nullable=False),
    sa.Column("store_id", sa.Text(), nullable=True),
    sa.Column("action_name", sa.String(128), nullable=False),
    sa.Column("risk", sa.String(32), nullable=False),
    sa.Column("requester_actor_id", sa.Text(), nullable=False),
    sa.Column("requester_actor_type", sa.String(32), nullable=False),
    sa.Column("request_id", sa.Uuid(), nullable=False),
    sa.Column("action_run_id", sa.Uuid(), nullable=False),
    sa.Column("subject_fingerprint", sa.CHAR(64), nullable=False),
    sa.Column("status", sa.String(16), nullable=False),
    sa.Column("summary", JSONB(), nullable=False),
    sa.Column("source_kind", sa.String(32), nullable=False),
    sa.Column("source_command_id", sa.Uuid(), nullable=True),
    sa.Column("source_workflow_run_id", sa.Uuid(), nullable=True),
    sa.Column("source_workflow_id", sa.String(128), nullable=True),
    sa.Column("source_workflow_step_id", sa.String(128), nullable=True),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("decided_by_actor_id", sa.Text(), nullable=True),
    sa.Column("decided_by_actor_type", sa.String(32), nullable=True),
    sa.Column("decision_note", sa.Text(), nullable=True),
    sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("consumed_by_action_run_id", sa.Uuid(), nullable=True),
    sa.Column("execution_outcome", sa.String(32), nullable=True),
    sa.PrimaryKeyConstraint("approval_id", name="pk_approval_requests"),
    sa.UniqueConstraint("company_id", "approval_id", name="uq_approval_requests_company"),
    sa.Index("ix_approval_requests_company_created", "company_id", "created_at", "approval_id"),
    sa.Index("ix_approval_requests_company_due", "company_id", "status", "expires_at"),
)  # fmt: skip

approval_events = sa.Table(
    "approval_events",
    product_metadata,
    sa.Column("approval_id", sa.Uuid(), nullable=False),
    sa.Column("company_id", sa.Text(), nullable=False),
    sa.Column("sequence", sa.Integer(), nullable=False),
    sa.Column("event_type", sa.String(32), nullable=False),
    sa.Column("status", sa.String(16), nullable=False),
    sa.Column("actor_id", sa.Text(), nullable=True),
    sa.Column("actor_type", sa.String(32), nullable=True),
    sa.Column("action_run_id", sa.Uuid(), nullable=True),
    sa.Column("execution_outcome", sa.String(32), nullable=True),
    sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("recorded_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
              nullable=False),
    sa.PrimaryKeyConstraint("approval_id", "sequence", name="pk_approval_events"),
    sa.ForeignKeyConstraint(
        ["company_id", "approval_id"],
        ["product.approval_requests.company_id", "product.approval_requests.approval_id"],
        name="fk_approval_events_request",
    ),
)  # fmt: skip

_r = approval_requests.c
_e = approval_events.c
_ERRORS = (sa_exc.SQLAlchemyError, OSError)
_EVENT_COLUMNS = tuple(c for c in approval_events.c if c.name != "recorded_at")


def _row(request: ApprovalRequest) -> dict[str, Any]:
    source = request.source
    return {
        "approval_id": request.approval_id, "company_id": request.company_id,
        "store_id": request.store_id, "action_name": request.action_name,
        "risk": request.risk.value, "requester_actor_id": request.requester_actor_id,
        "requester_actor_type": request.requester_actor_type,
        "request_id": request.request_id, "action_run_id": request.action_run_id,
        "subject_fingerprint": request.subject_fingerprint, "status": request.status.value,
        "summary": request.summary.model_dump(mode="json"), "source_kind": source.kind.value,
        "source_command_id": source.command_id,
        "source_workflow_run_id": source.workflow_run_id,
        "source_workflow_id": source.workflow_id,
        "source_workflow_step_id": source.workflow_step_id,
        "created_at": request.created_at, "expires_at": request.expires_at,
        "decided_at": request.decided_at, "decided_by_actor_id": request.decided_by_actor_id,
        "decided_by_actor_type": request.decided_by_actor_type,
        "decision_note": request.decision_note, "consumed_at": request.consumed_at,
        "consumed_by_action_run_id": request.consumed_by_action_run_id,
        "execution_outcome": None if request.execution_outcome is None
        else request.execution_outcome.value,
    }  # fmt: skip


def _request(row: Mapping[str, Any]) -> ApprovalRequest:
    try:
        data = dict(row)
        source = ApprovalSourceRef(
            kind=data.pop("source_kind"), command_id=data.pop("source_command_id"),
            workflow_run_id=data.pop("source_workflow_run_id"),
            workflow_id=data.pop("source_workflow_id"),
            workflow_step_id=data.pop("source_workflow_step_id"),
        )  # fmt: skip
        data["summary"] = ApprovalSummary.model_validate(data["summary"])
        return ApprovalRequest.model_validate({**data, "source": source})
    except (ValidationError, TypeError, ValueError, KeyError):
        raise ApprovalRepositoryError() from None


def _event(row: Mapping[str, Any]) -> ApprovalEvent:
    try:
        return ApprovalEvent.model_validate({k: row[k] for k in (c.name for c in _EVENT_COLUMNS)})
    except (ValidationError, TypeError, ValueError, KeyError):
        raise ApprovalRepositoryError() from None


def _next_sequence(approval_id: UUID) -> sa.ScalarSelect[Any]:
    return (sa.select(sa.func.coalesce(sa.func.max(_e.sequence), 0) + 1)
            .where(_e.approval_id == approval_id).scalar_subquery())  # fmt: skip


def _append(approval_id: UUID, company_id: str, event_type: ApprovalEventType,
            status: ApprovalStatus, at: datetime, **fields: Any) -> sa.Insert:  # fmt: skip
    """The next append-only event of a request (its row is locked by the caller's CAS)."""
    return sa.insert(approval_events).values(
        approval_id=approval_id, company_id=company_id, sequence=_next_sequence(approval_id),
        event_type=event_type.value, status=status.value, occurred_at=at, **fields,
    )  # fmt: skip


class PostgresApprovalRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = session_factory

    async def create(self, request: ApprovalRequest) -> None:
        if request.status is not ApprovalStatus.REQUESTED:
            raise ApprovalRepositoryError()
        try:
            async with self._sessions() as session, session.begin():
                await session.execute(sa.insert(approval_requests).values(_row(request)))
                await session.execute(sa.insert(approval_events).values(
                    approval_id=request.approval_id, company_id=request.company_id, sequence=1,
                    event_type=ApprovalEventType.REQUESTED.value,
                    status=ApprovalStatus.REQUESTED.value, actor_id=request.requester_actor_id,
                    actor_type=request.requester_actor_type,
                    action_run_id=request.action_run_id, occurred_at=request.created_at,
                ))  # fmt: skip
        except _ERRORS:
            raise ApprovalRepositoryError() from None

    async def expire_due(self, company_id: str, now: datetime,
                         approval_id: UUID | None = None) -> int:  # fmt: skip
        due = (
            sa.update(approval_requests)
            .where(_r.company_id == company_id, _r.status == ApprovalStatus.REQUESTED.value,
                   _r.expires_at <= now)
            .values(status=ApprovalStatus.EXPIRED.value, decided_at=now)
            .returning(_r.approval_id)
        )  # fmt: skip
        if approval_id is not None:
            due = due.where(_r.approval_id == approval_id)
        try:
            async with self._sessions() as session, session.begin():
                expired = (await session.execute(due)).scalars().all()
                for expired_id in expired:
                    await session.execute(_append(expired_id, company_id,
                                                  ApprovalEventType.EXPIRED,
                                                  ApprovalStatus.EXPIRED, now))  # fmt: skip
        except _ERRORS:
            raise ApprovalRepositoryError() from None
        return len(expired)

    async def get(self, company_id: str, approval_id: UUID) -> ApprovalRequest | None:
        query = sa.select(approval_requests).where(
            _r.company_id == company_id, _r.approval_id == approval_id
        )
        try:
            async with self._sessions() as session:
                row = (await session.execute(query)).mappings().first()
        except _ERRORS:
            raise ApprovalRepositoryError() from None
        return None if row is None else _request(row)

    async def list(
        self, company_id: str, *, status: ApprovalStatus | None, action_name: str | None,
        limit: int,
    ) -> tuple[ApprovalRequest, ...]:  # fmt: skip
        query = (sa.select(approval_requests).where(_r.company_id == company_id)
                 .order_by(_r.created_at.desc(), _r.approval_id)
                 .limit(max(1, min(limit, 100))))  # fmt: skip
        if status is not None:
            query = query.where(_r.status == status.value)
        if action_name is not None:
            query = query.where(_r.action_name == action_name)
        try:
            async with self._sessions() as session:
                rows = (await session.execute(query)).mappings().all()
        except _ERRORS:
            raise ApprovalRepositoryError() from None
        return tuple(_request(row) for row in rows)

    async def events(self, company_id: str, approval_id: UUID) -> tuple[ApprovalEvent, ...]:
        query = (sa.select(*_EVENT_COLUMNS)
                 .where(_e.company_id == company_id, _e.approval_id == approval_id)
                 .order_by(_e.sequence))  # fmt: skip
        try:
            async with self._sessions() as session:
                rows = (await session.execute(query)).mappings().all()
        except _ERRORS:
            raise ApprovalRepositoryError() from None
        return tuple(_event(row) for row in rows)

    async def decide(
        self, company_id: str, approval_id: UUID, status: ApprovalStatus, actor_id: str,
        actor_type: str, note: str | None, now: datetime,
    ) -> ApprovalRequest:  # fmt: skip
        if status not in HUMAN_DECISIONS or actor_type == "system_agent":
            raise ApprovalTransitionConflictError()
        statement = (
            sa.update(approval_requests)
            .where(_r.company_id == company_id, _r.approval_id == approval_id,
                   _r.status == ApprovalStatus.REQUESTED.value, _r.expires_at > now)
            .values(status=status.value, decided_at=now, decided_by_actor_id=actor_id,
                    decided_by_actor_type=actor_type, decision_note=note)
            .returning(*approval_requests.c)
        )  # fmt: skip
        if status is not ApprovalStatus.CANCELLED:  # two-person rule, also in SQL
            statement = statement.where(_r.requester_actor_id != actor_id)
        try:
            async with self._sessions() as session, session.begin():
                row = (await session.execute(statement)).mappings().first()
                if row is None:
                    raise ApprovalTransitionConflictError()
                await session.execute(_append(approval_id, company_id, EVENT_FOR_STATUS[status],
                                              status, now, actor_id=actor_id,
                                              actor_type=actor_type))  # fmt: skip
        except _ERRORS:
            raise ApprovalRepositoryError() from None
        return _request(row)

    async def claim(
        self, company_id: str, approval_id: UUID, *, store_id: str | None, action_name: str,
        requester_actor_id: str, subject_fingerprint: str, action_run_id: UUID,
        now: datetime,
    ) -> ApprovalClaimStatus:  # fmt: skip
        statement = (
            sa.update(approval_requests)
            .where(
                _r.company_id == company_id, _r.approval_id == approval_id,
                _r.status == ApprovalStatus.APPROVED.value, _r.consumed_at.is_(None),
                _r.expires_at > now, _r.action_name == action_name,
                _r.requester_actor_id == requester_actor_id,
                _r.store_id.is_not_distinct_from(store_id),
                _r.subject_fingerprint == subject_fingerprint,
            )
            .values(consumed_at=now, consumed_by_action_run_id=action_run_id)
            .returning(_r.approval_id)
        )  # fmt: skip
        try:
            async with self._sessions() as session, session.begin():
                claimed = (await session.execute(statement)).scalar_one_or_none()
                if claimed is not None:
                    await session.execute(_append(
                        approval_id, company_id, ApprovalEventType.EXECUTION_CLAIMED,
                        ApprovalStatus.APPROVED, now, actor_id=requester_actor_id,
                        action_run_id=action_run_id))  # fmt: skip
                    return ApprovalClaimStatus.CLAIMED
                row = (await session.execute(sa.select(approval_requests).where(
                    _r.company_id == company_id, _r.approval_id == approval_id,
                ))).mappings().first()  # fmt: skip
        except _ERRORS:
            raise ApprovalRepositoryError() from None
        if row is None:
            return ApprovalClaimStatus.NOT_FOUND
        current = _request(row)
        if (current.action_name, current.requester_actor_id, current.store_id,
                current.subject_fingerprint) != (action_name, requester_actor_id, store_id,
                                                 subject_fingerprint):  # fmt: skip
            return ApprovalClaimStatus.MISMATCH
        return _refusal(current, now)

    async def record_execution(
        self, company_id: str, approval_id: UUID, action_run_id: UUID,
        outcome: ApprovalOutcome, now: datetime,
    ) -> None:  # fmt: skip
        statement = (
            sa.update(approval_requests)
            .where(_r.company_id == company_id, _r.approval_id == approval_id,
                   _r.consumed_by_action_run_id == action_run_id,
                   _r.execution_outcome.is_(None))
            .values(execution_outcome=ApprovalOutcome(outcome).value)
            .returning(_r.approval_id)
        )  # fmt: skip
        try:
            async with self._sessions() as session, session.begin():
                if (await session.execute(statement)).scalar_one_or_none() is not None:
                    await session.execute(_append(
                        approval_id, company_id, ApprovalEventType.EXECUTION_COMPLETED,
                        ApprovalStatus.APPROVED, now, action_run_id=action_run_id,
                        execution_outcome=ApprovalOutcome(outcome).value))  # fmt: skip
        except _ERRORS:
            raise ApprovalRepositoryError() from None


def _refusal(current: ApprovalRequest, now: datetime) -> ApprovalClaimStatus:
    """Why a matching request could not be consumed (nothing was changed)."""
    if current.status is ApprovalStatus.REQUESTED:
        return ApprovalClaimStatus.NOT_DECIDED
    if current.status is ApprovalStatus.REJECTED:
        return ApprovalClaimStatus.REJECTED
    if current.status is ApprovalStatus.CANCELLED:
        return ApprovalClaimStatus.CANCELLED
    if current.status is ApprovalStatus.EXPIRED:
        return ApprovalClaimStatus.EXPIRED
    if current.consumed:
        return ApprovalClaimStatus.ALREADY_CONSUMED
    if current.expires_at <= now:
        return ApprovalClaimStatus.EXPIRED  # granted, but no longer usable
    return ApprovalClaimStatus.NOT_DECIDED  # unreachable for a consistent row: fail closed
