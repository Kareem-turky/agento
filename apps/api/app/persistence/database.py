"""Explicit async database infrastructure for product-owned persistence.

No module-global engine or session: callers build an ``AsyncEngine`` and an
``async_sessionmaker`` and pass them to stores explicitly. Product tables live in the
``product`` schema, owned by Alembic migrations (``apps/api/migrations``); Agno's
``agno_runtime`` schema is never touched here. Nothing here creates or migrates
tables: API startup never changes the schema.
"""

from sqlalchemy import MetaData
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

PRODUCT_SCHEMA = "product"

# Describes the migrated tables for queries (and for Alembic's autogenerate
# comparison). It is never used to create them.
product_metadata = MetaData(schema=PRODUCT_SCHEMA)


def create_product_engine(database_url: str, **engine_options: object) -> AsyncEngine:
    """Async engine for ``APP_DATABASE_URL`` (``postgresql+psycopg://``)."""
    return create_async_engine(database_url, pool_pre_ping=True, **engine_options)


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)
