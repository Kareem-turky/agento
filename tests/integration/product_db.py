"""Helpers for product-schema integration tests (real PostgreSQL, real migrations)."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import sqlalchemy as sa
from alembic.config import Config

from app.persistence import PostgresWriteCommandStore, create_product_engine, create_session_factory

ROOT = Path(__file__).resolve().parents[2]


def alembic_config(database_url: str) -> Config:
    """The repository's alembic.ini. env.py reads the target from APP_DATABASE_URL (the
    same variable ``database_url`` came from); refuse to run against anything else."""
    from app.config import Settings

    if str(Settings().database_url) != database_url:
        raise RuntimeError("APP_DATABASE_URL does not match the integration database")
    return Config(str(ROOT / "alembic.ini"))


@asynccontextmanager
async def product_store(
    database_url: str, **engine_options
) -> AsyncIterator[PostgresWriteCommandStore]:
    """A store with its OWN engine and connection pool (a separate 'process')."""
    engine = create_product_engine(database_url, **engine_options)
    try:
        yield PostgresWriteCommandStore(create_session_factory(engine))
    finally:
        await engine.dispose()


def command_rows(engine: sa.Engine, company_id: str) -> list[dict]:
    with engine.connect() as connection:
        rows = connection.execute(
            sa.text(
                "SELECT * FROM product.write_commands WHERE company_id = :c ORDER BY created_at"
            ),
            {"c": company_id},
        ).mappings()
        return [dict(row) for row in rows]


def rows_for_key(engine: sa.Engine, key: str) -> list[dict]:
    """Every product.write_commands row whose key hash is SHA-256(key)."""
    from app.commands import hash_idempotency_key

    with engine.connect() as connection:
        rows = connection.execute(
            sa.text("SELECT * FROM product.write_commands WHERE idempotency_key_hash = :h"),
            {"h": hash_idempotency_key(key)},
        ).mappings()
        return [dict(row) for row in rows]
