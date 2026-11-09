from logging.config import fileConfig

from alembic import context
from sqlmodel import SQLModel

import app.models  # noqa: F401  (registers the tables on SQLModel.metadata)
from app.core.config import get_settings
from app.core.db import make_engine

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = SQLModel.metadata


def run_migrations_online() -> None:
    settings = get_settings()
    schema = settings.DATABASE_SCHEMA
    # The engine sets search_path to the service schema on every connection, so
    # tables are created there and the version table lives next to them.
    engine = make_engine(settings.DATABASE_URL, schema)
    with engine.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            version_table_schema=schema,
            # Tables carry no schema qualifier; autogenerate must compare against
            # the search_path schema only.
            include_schemas=False,
            compare_type=True,
            # The version table is not a model; keep autogenerate away from it.
            include_object=lambda _obj, name, type_, _reflected, _compare_to: (
                not (type_ == "table" and name == "alembic_version")
            ),
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    raise SystemExit("offline mode is not supported: migrations need a live database")
run_migrations_online()
