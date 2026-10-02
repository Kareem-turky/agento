"""PostgreSQL implementation of ``IntegrationConnectionRepository``
(table ``product.integration_connections``, owned by migration 0003; never created here).

Connection METADATA only: no secret value and no business data. One short transaction
per call; every query is scoped by the trusted company id. Rows are rebuilt strictly
through ``IntegrationConnection`` (invalid stored data fails closed). Every failure is a
``ConnectionRepositoryError`` with a fixed message: SQL, URLs, driver errors and stored
values never cross this boundary (exceptions are not chained).
"""

from collections.abc import Iterable
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from pydantic import ValidationError
from sqlalchemy import exc as sa_exc
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.integration_management.connections import (
    ConnectionErrorCode,
    ConnectionRepositoryError,
    ConnectionTestResult,
    IntegrationConnection,
)
from app.persistence.database import product_metadata


def _in(column: str, values: Iterable[str]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


# Mirrors migration 0003 (``alembic check`` and the vocabulary test keep them in step).
integration_connections = sa.Table(
    "integration_connections",
    product_metadata,
    sa.Column("connection_id", sa.Uuid(), nullable=False),
    sa.Column("company_id", sa.Text(), nullable=False),
    sa.Column("integration_id", sa.String(64), nullable=False),
    sa.Column("display_name", sa.Text(), nullable=False),
    sa.Column("config", JSONB(), nullable=False),
    sa.Column("secret_fields", JSONB(), nullable=False),
    sa.Column("enabled", sa.Boolean(), nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("last_tested_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("last_test_result", sa.String(16), nullable=False),
    sa.Column("last_test_error", sa.String(64), nullable=True),
    sa.PrimaryKeyConstraint("connection_id", name="pk_integration_connections"),
    sa.CheckConstraint("integration_id ~ '^[a-z0-9][a-z0-9-]{0,62}[a-z0-9]$'",
                       name="ck_integration_connections_integration_id"),
    sa.CheckConstraint("char_length(display_name) BETWEEN 1 AND 120",
                       name="ck_integration_connections_display_name"),
    sa.CheckConstraint("jsonb_typeof(config) = 'object'",
                       name="ck_integration_connections_config"),
    sa.CheckConstraint("jsonb_typeof(secret_fields) = 'array'",
                       name="ck_integration_connections_secret_fields"),
    sa.CheckConstraint(_in("last_test_result", (r.value for r in ConnectionTestResult)),
                       name="ck_integration_connections_last_test_result"),
    sa.CheckConstraint("last_test_error IS NULL OR "
                       + _in("last_test_error", (c.value for c in ConnectionErrorCode)),
                       name="ck_integration_connections_last_test_error"),
    sa.CheckConstraint("(last_test_result = 'never_tested') = (last_tested_at IS NULL)",
                       name="ck_integration_connections_tested_at"),
    sa.CheckConstraint("(last_test_result = 'failure') = (last_test_error IS NOT NULL)",
                       name="ck_integration_connections_test_error"),
    sa.Index("ix_integration_connections_company_id_created_at",
             "company_id", "created_at", "connection_id"),
)  # fmt: skip

_c = integration_connections.c


def _row(connection: IntegrationConnection) -> dict[str, Any]:
    return {
        "connection_id": connection.connection_id,
        "company_id": connection.company_id,
        "integration_id": connection.integration_id,
        "display_name": connection.display_name,
        "config": dict(connection.config),
        "secret_fields": sorted(connection.secret_fields),
        "enabled": connection.enabled,
        "created_at": connection.created_at,
        "updated_at": connection.updated_at,
        "last_tested_at": connection.last_tested_at,
        "last_test_result": connection.last_test_result.value,
        "last_test_error": None if connection.last_test_error is None
                           else connection.last_test_error.value,
    }  # fmt: skip


def _connection(row: sa.RowMapping) -> IntegrationConnection:
    try:
        data = dict(row)
        fields = data["secret_fields"]
        if not isinstance(fields, list):
            raise TypeError("secret_fields must be a list")
        data["secret_fields"] = frozenset(fields)
        return IntegrationConnection.model_validate(data)
    except (ValidationError, KeyError, TypeError, ValueError):
        raise ConnectionRepositoryError() from None


class PostgresIntegrationConnectionRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = session_factory

    async def insert(self, connection: IntegrationConnection) -> None:
        try:
            async with self._sessions() as session, session.begin():
                await session.execute(sa.insert(integration_connections).values(_row(connection)))
        except (sa_exc.SQLAlchemyError, OSError):
            raise ConnectionRepositoryError() from None

    async def get(self, company_id: str, connection_id: UUID) -> IntegrationConnection | None:
        query = sa.select(integration_connections).where(
            _c.company_id == company_id, _c.connection_id == connection_id
        )
        try:
            async with self._sessions() as session:
                row = (await session.execute(query)).mappings().first()
        except (sa_exc.SQLAlchemyError, OSError):
            raise ConnectionRepositoryError() from None
        return None if row is None else _connection(row)

    async def list(self, company_id: str) -> tuple[IntegrationConnection, ...]:
        query = (sa.select(integration_connections).where(_c.company_id == company_id)
                 .order_by(_c.created_at, _c.connection_id))  # fmt: skip
        try:
            async with self._sessions() as session:
                rows = (await session.execute(query)).mappings().all()
        except (sa_exc.SQLAlchemyError, OSError):
            raise ConnectionRepositoryError() from None
        return tuple(_connection(row) for row in rows)

    async def update(self, connection: IntegrationConnection) -> bool:
        values = _row(connection)
        for immutable in ("connection_id", "company_id", "integration_id", "created_at"):
            values.pop(immutable)
        statement = (
            sa.update(integration_connections)
            .where(_c.company_id == connection.company_id,
                   _c.connection_id == connection.connection_id)
            .values(values)
        )  # fmt: skip
        try:
            async with self._sessions() as session, session.begin():
                result = await session.execute(statement)
        except (sa_exc.SQLAlchemyError, OSError):
            raise ConnectionRepositoryError() from None
        return bool(getattr(result, "rowcount", 0))

    async def delete(self, company_id: str, connection_id: UUID) -> bool:
        statement = sa.delete(integration_connections).where(
            _c.company_id == company_id, _c.connection_id == connection_id
        )
        try:
            async with self._sessions() as session, session.begin():
                result = await session.execute(statement)
        except (sa_exc.SQLAlchemyError, OSError):
            raise ConnectionRepositoryError() from None
        return bool(getattr(result, "rowcount", 0))
