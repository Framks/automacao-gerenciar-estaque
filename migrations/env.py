"""Ambiente do Alembic: usa a mesma URL e o mesmo schema do app."""

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool

from app.db.engine import url_sqlalchemy
from app.db.modelos import Base

config = context.config
if config.config_file_name is not None and "url" not in config.attributes:  # nos testes, não mexe no logging
    fileConfig(config.config_file_name)


def _url() -> str:
    url = config.attributes.get("url") or context.get_x_argument(as_dictionary=True).get("url") \
        or os.environ.get("BOT_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if not url:
        from app.config import get_settings

        url = get_settings().banco_url
    if not url:
        raise RuntimeError("defina BOT_DATABASE_URL ou DATABASE_URL (ou alembic -x url=...)")
    return url_sqlalchemy(url)


def run_migrations_offline() -> None:
    context.configure(url=_url(), target_metadata=Base.metadata, literal_binds=True, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    conectavel = create_engine(_url(), poolclass=pool.NullPool)
    with conectavel.connect() as conexao:
        context.configure(connection=conexao, target_metadata=Base.metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
