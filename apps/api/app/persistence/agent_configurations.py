"""PostgreSQL implementation of ``AgentConfigurationRepository``
(table ``product.agent_configurations``, owned by migration 0004; never created here).

Override rows only (enabled flag + timestamps). One short transaction per call; every
query is scoped by the trusted company id. Rows are rebuilt strictly through
``AgentConfiguration`` (invalid stored data fails closed). Every failure is an
``AgentConfigurationRepositoryError`` with a fixed message (exceptions are not chained).
"""

import sqlalchemy as sa
from pydantic import AwareDatetime, ValidationError
from sqlalchemy import exc as sa_exc
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agent_management.configuration import (
    AgentConfiguration,
    AgentConfigurationRepositoryError,
)
from app.persistence.database import product_metadata

# Mirrors migration 0004 (``alembic check`` keeps them in step).
agent_configurations = sa.Table(
    "agent_configurations",
    product_metadata,
    sa.Column("company_id", sa.Text(), nullable=False),
    sa.Column("agent_id", sa.String(64), nullable=False),
    sa.Column("enabled", sa.Boolean(), nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint("company_id", "agent_id", name="pk_agent_configurations"),
    sa.CheckConstraint("agent_id ~ '^[a-z][a-z0-9-]{0,62}[a-z0-9]$'",
                       name="ck_agent_configurations_agent_id"),
    sa.CheckConstraint("char_length(company_id) BETWEEN 1 AND 200",
                       name="ck_agent_configurations_company_id"),
    sa.CheckConstraint("updated_at >= created_at", name="ck_agent_configurations_updated_at"),
)  # fmt: skip

_c = agent_configurations.c


def _configuration(row: sa.RowMapping) -> AgentConfiguration:
    try:
        return AgentConfiguration.model_validate(dict(row))
    except (ValidationError, TypeError, ValueError):
        raise AgentConfigurationRepositoryError() from None


class PostgresAgentConfigurationRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = session_factory

    async def get(self, company_id: str, agent_id: str) -> AgentConfiguration | None:
        query = sa.select(agent_configurations).where(
            _c.company_id == company_id, _c.agent_id == agent_id
        )
        try:
            async with self._sessions() as session:
                row = (await session.execute(query)).mappings().first()
        except (sa_exc.SQLAlchemyError, OSError):
            raise AgentConfigurationRepositoryError() from None
        return None if row is None else _configuration(row)

    async def list(self, company_id: str) -> tuple[AgentConfiguration, ...]:
        query = (sa.select(agent_configurations).where(_c.company_id == company_id)
                 .order_by(_c.agent_id))  # fmt: skip
        try:
            async with self._sessions() as session:
                rows = (await session.execute(query)).mappings().all()
        except (sa_exc.SQLAlchemyError, OSError):
            raise AgentConfigurationRepositoryError() from None
        return tuple(_configuration(row) for row in rows)

    async def set_enabled(
        self, company_id: str, agent_id: str, enabled: bool, at: AwareDatetime
    ) -> AgentConfiguration:
        statement = (
            insert(agent_configurations)
            .values(company_id=company_id, agent_id=agent_id, enabled=enabled,
                    created_at=at, updated_at=at)
            .on_conflict_do_update(
                constraint="pk_agent_configurations",
                set_={"enabled": enabled, "updated_at": sa.func.greatest(_c.created_at, at)},
            )
            .returning(*agent_configurations.c)
        )  # fmt: skip
        try:
            async with self._sessions() as session, session.begin():
                row = (await session.execute(statement)).mappings().one()
        except (sa_exc.SQLAlchemyError, OSError):
            raise AgentConfigurationRepositoryError() from None
        return _configuration(row)

    async def delete(self, company_id: str, agent_id: str) -> bool:
        statement = sa.delete(agent_configurations).where(
            _c.company_id == company_id, _c.agent_id == agent_id
        )
        try:
            async with self._sessions() as session, session.begin():
                result = await session.execute(statement)
        except (sa_exc.SQLAlchemyError, OSError):
            raise AgentConfigurationRepositoryError() from None
        return bool(getattr(result, "rowcount", 0))
