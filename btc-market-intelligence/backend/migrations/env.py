import os
import re
import time
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool, text

from app.database.models import Base

_SCHEMA = os.environ.get("BMI_MIGRATION_SCHEMA", "")
if _SCHEMA and not re.fullmatch(r"[a-z_][a-z0-9_]*", _SCHEMA):
    raise RuntimeError("BMI_MIGRATION_SCHEMA must be a plain identifier")

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True, dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    section = config.get_section(config.config_ini_section, {})
    section["sqlalchemy.url"] = config.get_main_option("sqlalchemy.url")
    connectable = engine_from_config(section, prefix="sqlalchemy.", poolclass=pool.NullPool)
    last_error = None
    connection = None
    for attempt in range(1, 4):
        try:
            connection = connectable.connect()
            break
        except Exception as exc:
            last_error = exc
            if "getaddrinfo" not in str(exc) or attempt == 3:
                raise
            time.sleep(2 ** (attempt - 1))
    if connection is None:
        raise last_error
    with connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            version_table_schema=_SCHEMA or None,
        )
        with context.begin_transaction():
            if _SCHEMA:
                connection.execute(text("SET LOCAL search_path TO %s" % _SCHEMA))
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
