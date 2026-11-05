"""Conexão com o Postgres (Neon), pensada para serverless: pool pequeno, conexão testada antes do uso."""

from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from app.errors import ServicoIndisponivel


def url_sqlalchemy(url: str) -> str:
    """postgres://... e postgresql://... viram postgresql+psycopg://... (driver psycopg 3)."""
    for prefixo in ("postgres://", "postgresql://"):
        if url.startswith(prefixo):
            return "postgresql+psycopg://" + url[len(prefixo):]
    return url


@lru_cache
def engine(url: str) -> Engine:
    return create_engine(
        url_sqlalchemy(url),
        pool_pre_ping=True,
        pool_size=1,
        max_overflow=2,
        pool_recycle=300,
        # prepare_threshold=None: sem prepared statements, que o pgbouncer (URL pooled do Neon) não guarda
        connect_args={"connect_timeout": 10, "prepare_threshold": None},
    )


@lru_cache
def fabrica(url: str) -> sessionmaker[Session]:
    return sessionmaker(engine(url), expire_on_commit=False)


@contextmanager
def transacao(fabrica_: sessionmaker[Session]) -> Iterator[Session]:
    """Uma unidade de trabalho: commit no fim, rollback em qualquer erro.

    Falha de conexão (Neon acordando, rede) vira ServicoIndisponivel: a fila tenta de novo.
    """
    sessao = fabrica_()
    try:
        yield sessao
        sessao.commit()
    except OperationalError as e:
        sessao.rollback()
        raise ServicoIndisponivel(f"Postgres: {str(e).splitlines()[0][:200]}") from e
    except BaseException:
        sessao.rollback()
        raise
    finally:
        sessao.close()


def banco_ok(url: str) -> bool:
    try:
        with engine(url).connect() as conexao:
            conexao.execute(text("SELECT 1"))
        return True
    except Exception:
        return False
