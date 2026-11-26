from alembic import context
from sqlalchemy import create_engine, pool

from atlas_api.anatomy import models  # noqa: F401 - register tables on shared metadata
from atlas_api.assets import models as asset_models  # noqa: F401 - register registry tables
from atlas_api.config import MIGRATION_DATABASE_ENV, MIGRATION_ROLE, read_database_url
from atlas_api.db import SCHEMA, metadata


def include_name(name, type_, parent_names):
    return type_ != "schema" or name == SCHEMA


def run_migrations(**connection_options) -> None:
    context.configure(
        **connection_options,
        target_metadata=metadata,
        version_table_schema=SCHEMA,
        include_schemas=True,
        include_name=include_name,
    )
    with context.begin_transaction():
        context.execute("SET ROLE atlas_owner")
        context.run_migrations()
        context.execute("RESET ROLE")


if context.is_offline_mode():
    run_migrations(dialect_name="postgresql")
else:
    url = read_database_url(MIGRATION_DATABASE_ENV, MIGRATION_ROLE)
    if url is None:
        raise RuntimeError(f"Set {MIGRATION_DATABASE_ENV} to run migrations online.")
    engine = create_engine(
        url,
        poolclass=pool.NullPool,
        connect_args={"connect_timeout": 3},
    )
    with engine.connect() as connection:
        run_migrations(connection=connection)
