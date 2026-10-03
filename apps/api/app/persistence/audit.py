"""PostgreSQL implementation of ``AuditSink`` (table ``product.audit_events``).

    ExecutionCoordinator -> AuditEvent -> PostgresAuditSink.record(event)
      -> one short transaction: INSERT exactly one row -> COMMIT -> return

``record`` returns only after the row is committed, so the pre-side-effect
``EXECUTION_STARTED`` event is durable before the handler runs. There is no
buffering, background flush, retry or upsert: events are append-only facts, and a
duplicate ``event_id`` fails. No transaction is ever held open across governance,
execution or verification, and no session is exposed to callers.

Only the explicit ``AuditEvent`` fields are stored (metadata only). Any failure raises
``AuditPersistenceError`` with a fixed message; driver, SQL and constraint details
never cross this boundary. The coordinator treats that as an audit failure (fail
closed). Nothing here creates tables: ``product.audit_events`` is owned by migration
0002.
"""

from collections.abc import Iterable
from typing import Any, get_args

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.context.models import ActorType, Channel
from app.execution.audit import AuditEvent, AuditEventType
from app.execution.models import ActionRunReason, ActionRunStatus
from app.governance.policy import PolicyOutcome, PolicyReason
from app.persistence.database import product_metadata


def _check(column: str, values: Iterable[str], *, nullable: bool) -> sa.CheckConstraint:
    """``column IN (...)`` (``column IS NULL OR ...`` when nullable), named as in 0002."""
    listed = ", ".join(f"'{v}'" for v in values)
    condition = f"{column} IN ({listed})"
    return sa.CheckConstraint(
        f"{column} IS NULL OR {condition}" if nullable else condition,
        name=f"ck_audit_events_{column}",
    )


# Mirrors the product.audit_events schema at the head migration (0002, plus migration
# 0007's nullable ``approval_id`` and its superset vocabularies for ``event_type`` and
# ``run_reason``), including its seven CHECK constraints; never created here. The
# allowed values come from the trusted contracts. A vocabulary change therefore needs a
# NEW migration replacing the constraints; 0002 is a frozen snapshot and is never edited
# (tests/persistence/test_audit_schema.py and the live-database parity test check this).
audit_events = sa.Table(
    "audit_events",
    product_metadata,
    sa.Column("event_id", sa.Uuid(), nullable=False),
    sa.Column("run_id", sa.Uuid(), nullable=False),
    sa.Column("request_id", sa.Uuid(), nullable=False),
    sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("event_type", sa.String(32), nullable=False),
    sa.Column("action_name", sa.Text(), nullable=False),
    sa.Column("actor_id", sa.Text(), nullable=True),
    sa.Column("actor_type", sa.String(32), nullable=True),
    sa.Column("company_id", sa.Text(), nullable=False),
    sa.Column("store_id", sa.Text(), nullable=True),
    sa.Column("channel", sa.String(32), nullable=False),
    sa.Column("policy_outcome", sa.String(32), nullable=True),
    sa.Column("policy_reason", sa.String(64), nullable=True),
    sa.Column("run_status", sa.String(32), nullable=True),
    sa.Column("run_reason", sa.String(64), nullable=True),
    sa.Column("execution_reference_id", sa.String(128), nullable=True),
    sa.Column("verification_code", sa.String(64), nullable=True),
    sa.Column("approval_id", sa.Uuid(), nullable=True),  # Task 036 (migration 0007)
    sa.Column(
        "recorded_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    ),
    sa.PrimaryKeyConstraint("event_id", name="pk_audit_events"),
    sa.Index("ix_audit_events_run_id", "run_id"),
    sa.Index("ix_audit_events_request_id", "request_id"),
    _check("event_type", (e.value for e in AuditEventType), nullable=False),
    _check("actor_type", get_args(ActorType), nullable=True),
    _check("channel", get_args(Channel), nullable=False),
    _check("policy_outcome", (e.value for e in PolicyOutcome), nullable=True),
    _check("policy_reason", (e.value for e in PolicyReason), nullable=True),
    _check("run_status", (e.value for e in ActionRunStatus), nullable=True),
    _check("run_reason", (e.value for e in ActionRunReason), nullable=True),
)


class AuditPersistenceError(Exception):
    """The audit event was not durably recorded. Fixed message: no driver details."""

    def __init__(self) -> None:
        super().__init__("audit event could not be persisted")


def _value(item: Any) -> Any:
    return None if item is None else getattr(item, "value", item)


def _row(event: AuditEvent) -> dict[str, Any]:
    """Exactly the explicit AuditEvent fields, nothing inferred or enriched."""
    return {
        "event_id": event.event_id,
        "run_id": event.run_id,
        "request_id": event.request_id,
        "occurred_at": event.occurred_at,
        "event_type": event.event_type.value,
        "action_name": event.action_name,
        "actor_id": event.actor_id,
        "actor_type": event.actor_type,
        "company_id": event.company_id,
        "store_id": event.store_id,
        "channel": event.channel,
        "policy_outcome": _value(event.policy_outcome),
        "policy_reason": _value(event.policy_reason),
        "run_status": _value(event.run_status),
        "run_reason": _value(event.run_reason),
        "execution_reference_id": event.execution_reference_id,
        "verification_code": event.verification_code,
        "approval_id": event.approval_id,
    }


class PostgresAuditSink:
    """Durable ``AuditSink``: one committed INSERT per event, append-only."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = session_factory

    async def record(self, event: AuditEvent) -> None:
        if not isinstance(event, AuditEvent):
            raise AuditPersistenceError()
        try:
            values = _row(event)
            async with self._sessions() as session, session.begin():
                await session.execute(sa.insert(audit_events).values(values))
            # The transaction has committed here: the event is durable.
        except Exception:  # noqa: BLE001 - fail closed without leaking driver details
            raise AuditPersistenceError() from None
