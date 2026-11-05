"""Fixtures compartilhadas. Os testes com banco usam TEST_DATABASE_URL (no CI, um serviço postgres:16).

Sem TEST_DATABASE_URL, esses testes são pulados. Localmente:
  docker run -d --name pg-bot-teste -e POSTGRES_PASSWORD=teste -e POSTGRES_DB=bot_teste -p 55432:5432 postgres:16
  export TEST_DATABASE_URL=postgresql://postgres:teste@localhost:55432/bot_teste
"""

import os

import pytest

URL_TESTE = os.environ.get("TEST_DATABASE_URL", "")


@pytest.fixture(scope="session")
def _banco_migrado():
    if not URL_TESTE:
        pytest.skip("TEST_DATABASE_URL não definida")
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import text

    from app.db.engine import engine

    with engine(URL_TESTE).begin() as c:  # banco limpo: as migrações são testadas do zero
        c.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public"))
    cfg = Config("alembic.ini")
    cfg.attributes["url"] = URL_TESTE  # lido por migrations/env.py
    command.upgrade(cfg, "head")
    return URL_TESTE


@pytest.fixture
def fabrica_pg(_banco_migrado):
    """Fábrica de sessões num banco migrado e vazio (as tabelas são esvaziadas antes de cada teste)."""
    from sqlalchemy import text

    from app.db.engine import engine, fabrica
    from app.db.modelos import Base

    tabelas = ", ".join(t.name for t in Base.metadata.sorted_tables)
    with engine(_banco_migrado).begin() as c:
        c.execute(text(f"TRUNCATE {tabelas} RESTART IDENTITY CASCADE"))
    return fabrica(_banco_migrado)
