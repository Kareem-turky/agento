"""Product persistence infrastructure (SQLAlchemy 2 async, PostgreSQL).

Implements product-owned store contracts: ``app.commands.WriteCommandStore`` /
``WriteCommandReader`` and ``app.execution.AuditSink`` (``PostgresAuditSink``). Depends
on SQLAlchemy, ``app.commands`` and ``app.execution.audit`` only: no agents, Agno,
routes, integrations or business handlers. The schema is owned by Alembic migrations,
never created here.
"""

from app.persistence.audit import AuditPersistenceError, PostgresAuditSink, audit_events
from app.persistence.database import (
    PRODUCT_SCHEMA,
    create_product_engine,
    create_session_factory,
    product_metadata,
)
from app.persistence.write_commands import PostgresWriteCommandStore, write_commands

__all__ = [
    "PRODUCT_SCHEMA",
    "AuditPersistenceError",
    "PostgresAuditSink",
    "audit_events",
    "PostgresWriteCommandStore",
    "create_product_engine",
    "create_session_factory",
    "product_metadata",
    "write_commands",
]
