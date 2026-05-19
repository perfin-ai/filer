from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from filer_backend.config import db_url
from filer_backend.storage.db import Base
import filer_backend.storage.models  # noqa: F401  # register models

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

config.set_main_option("sqlalchemy.url", db_url())

target_metadata = Base.metadata


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    raise RuntimeError("offline migrations not supported")
else:
    run_migrations_online()
