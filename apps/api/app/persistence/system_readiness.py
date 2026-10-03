"""PostgreSQL readiness probe of the Product (Task 039).

    one bounded check (default 2 s, no retry):
      fresh connection  -> SELECT 1                           else UNAVAILABLE
                        -> to_regclass('product.alembic_version')
                             missing                          -> MISMATCH
                        -> SELECT version_num                 exactly [expected] -> READY
                                                              anything else      -> MISMATCH

Every check opens a FRESH connection (``NullPool``): an outage is seen at once and a
recovered database is seen on the very next check, with no stale pooled connection and
no application restart. Read-only: it never migrates, writes or reads business data,
and every failure maps to a fixed state (no URL, host, port, SQL or driver text is
returned, stored or logged).
"""

import asyncio

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.pool import NullPool

from app.persistence.database import PRODUCT_SCHEMA, create_product_engine
from app.system_operations.models import DATABASE_UNAVAILABLE, ComponentState, DatabaseCheck

DEFAULT_TIMEOUT_SECONDS = 2.0
VERSION_TABLE = f"{PRODUCT_SCHEMA}.alembic_version"
_TABLE_EXISTS = text("SELECT to_regclass(:table) IS NOT NULL")
_REVISIONS = text(f"SELECT version_num FROM {VERSION_TABLE}")  # noqa: S608 - fixed constant


def create_readiness_engine(database_url: str, *, timeout_seconds: float) -> AsyncEngine:
    """An engine for the probe only: no pool, a bounded connect timeout."""
    return create_product_engine(
        database_url,
        poolclass=NullPool,
        connect_args={"connect_timeout": max(1, int(timeout_seconds))},
    )


class PostgresReadinessProbe:
    def __init__(
        self,
        engine: AsyncEngine,
        *,
        expected_revision: str,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._engine = engine
        self._expected = expected_revision
        self._timeout = timeout_seconds

    async def check(self) -> DatabaseCheck:
        try:
            async with asyncio.timeout(self._timeout):
                async with self._engine.connect() as connection:
                    await connection.execute(text("SELECT 1"))
                    exists = (await connection.execute(
                        _TABLE_EXISTS, {"table": VERSION_TABLE})).scalar_one()  # fmt: skip
                    if not exists:
                        return DatabaseCheck(ComponentState.READY, ComponentState.MISMATCH)
                    revisions = list((await connection.execute(_REVISIONS)).scalars())
        except Exception:  # noqa: BLE001 - timeout, refused, auth, driver: all "unavailable"
            return DATABASE_UNAVAILABLE
        schema = ComponentState.READY if revisions == [self._expected] else ComponentState.MISMATCH
        return DatabaseCheck(ComponentState.READY, schema)
