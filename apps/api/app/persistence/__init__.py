"""Product persistence infrastructure (SQLAlchemy 2 async, PostgreSQL).

Implements product-owned store contracts: ``app.commands.WriteCommandStore`` /
``WriteCommandReader``, ``app.execution.AuditSink`` (``PostgresAuditSink``) and
``app.integration_management.IntegrationConnectionRepository`` (connection METADATA,
Task 031). Depends on SQLAlchemy and those contracts only: no agents, Agno, routes,
business integrations or business handlers, and no business-data tables. The schema
is owned by Alembic migrations, never created here.
"""

from app.persistence.audit import AuditPersistenceError, PostgresAuditSink, audit_events
from app.persistence.database import (
    PRODUCT_SCHEMA,
    create_product_engine,
    create_session_factory,
    product_metadata,
)
from app.persistence.integration_connections import (
    PostgresIntegrationConnectionRepository,
    integration_connections,
)
from app.persistence.write_commands import PostgresWriteCommandStore, write_commands

__all__ = [
    "PRODUCT_SCHEMA",
    "AuditPersistenceError",
    "PostgresAuditSink",
    "PostgresIntegrationConnectionRepository",
    "audit_events",
    "PostgresWriteCommandStore",
    "create_product_engine",
    "integration_connections",
    "create_session_factory",
    "product_metadata",
    "write_commands",
]
