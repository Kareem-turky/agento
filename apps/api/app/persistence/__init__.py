"""Product persistence infrastructure (SQLAlchemy 2 async, PostgreSQL).

Implements product-owned store contracts: ``app.commands.WriteCommandStore`` /
``WriteCommandReader``, ``app.execution.AuditSink`` (``PostgresAuditSink``) and the
canonical commerce store ``app.commerce.store.CommerceStoreReader``
(``PostgresCommerceStore``, Task 031). Depends on SQLAlchemy, those contracts and the
canonical commerce domain only: no agents, Agno, routes, integrations or business
handlers. The schema is owned by Alembic migrations, never created here.
"""

from app.persistence.audit import AuditPersistenceError, PostgresAuditSink, audit_events
from app.persistence.commerce import COMMERCE_TABLES, PostgresCommerceStore
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
    "COMMERCE_TABLES",
    "PostgresCommerceStore",
    "PostgresAuditSink",
    "audit_events",
    "PostgresWriteCommandStore",
    "create_product_engine",
    "create_session_factory",
    "product_metadata",
    "write_commands",
]
