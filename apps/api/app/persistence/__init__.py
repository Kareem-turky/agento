"""Product persistence infrastructure (SQLAlchemy 2 async, PostgreSQL).

Implements product-owned store contracts (today ``app.commands.WriteCommandStore``).
Depends on SQLAlchemy and ``app.commands`` only: no agents, Agno, routes, integrations
or business handlers. The schema is owned by Alembic migrations, never created here.
"""

from app.persistence.database import (
    PRODUCT_SCHEMA,
    create_product_engine,
    create_session_factory,
    product_metadata,
)
from app.persistence.write_commands import PostgresWriteCommandStore, write_commands

__all__ = [
    "PRODUCT_SCHEMA",
    "PostgresWriteCommandStore",
    "create_product_engine",
    "create_session_factory",
    "product_metadata",
    "write_commands",
]
