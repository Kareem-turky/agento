"""Alembic environment for the product schema.

Target database: ``APP_DATABASE_URL`` (product settings; no second database URL).
Scope: only the ``product`` schema. The version table lives there too, and Agno's
``agno_runtime`` schema is excluded from every comparison and never modified.
"""

from alembic import context
from sqlalchemy import create_engine, pool, text

from app.config import Settings
from app.persistence.agent_configurations import (  # noqa: F401 - registers the table
    agent_configurations,
)
from app.persistence.audit import audit_events  # noqa: F401 - registers the table
from app.persistence.database import PRODUCT_SCHEMA, product_metadata
from app.persistence.integration_connections import (  # noqa: F401 - registers the table
    integration_connections,
)
from app.persistence.write_commands import write_commands  # noqa: F401 - registers the table

VERSION_TABLE = "alembic_version"


def _database_url() -> str:
    url = Settings().database_url
    if url is None:
        raise RuntimeError("APP_DATABASE_URL must be set to run product migrations")
    return str(url)


def _include_object(obj, name, type_, reflected, compare_to) -> bool:
    schema = getattr(obj, "schema", None)
    if type_ == "table":
        return schema == PRODUCT_SCHEMA
    return True


def _configure(**options) -> None:
    context.configure(
        target_metadata=product_metadata,
        version_table=VERSION_TABLE,
        version_table_schema=PRODUCT_SCHEMA,
        include_schemas=True,
        include_object=_include_object,
        **options,
    )


def run_migrations_offline() -> None:
    _configure(url=_database_url(), literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(_database_url(), poolclass=pool.NullPool)
    try:
        with engine.connect() as connection:
            # The version table lives in the product schema, so it must exist first.
            connection.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{PRODUCT_SCHEMA}"'))
            connection.commit()
            _configure(connection=connection)
            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
