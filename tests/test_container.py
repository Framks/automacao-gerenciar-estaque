"""O container monta o pipeline real (sem rede)."""

import pytest

from app import container
from app.config import get_settings
from app.storage.pg import PgComerciantes
from app.storage.pg_conversa import PgLoja, PgMensagens


@pytest.fixture
def limpar_caches(monkeypatch):
    for nome in ("ARMAZENAMENTO", "BOT_DATABASE_URL", "DATABASE_URL", "UPSTASH_REDIS_REST_URL", "QSTASH_TOKEN"):
        monkeypatch.delenv(nome, raising=False)
    monkeypatch.setattr("app.config.Settings.model_config", {**get_settings().model_config, "env_file": None})

    def limpar():
        for f in (get_settings, container.redis, container.controle, container.fila, container.pipeline,
                  container.comerciantes, container.mensagens, container.configuracoes, container.acesso):
            f.cache_clear()

    limpar()
    yield
    limpar()


def test_pipeline_com_postgres(monkeypatch, limpar_caches):
    monkeypatch.setenv("ARMAZENAMENTO", "postgres")
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@localhost:1/x")
    p = container.pipeline()
    assert isinstance(p._tenants, PgComerciantes) and isinstance(p._mensagens, PgMensagens)
    assert isinstance(p._loja(type("C", (), {"id": 1, "telefone": "1"})()), PgLoja)


def test_sem_postgres_falha_cedo(monkeypatch, limpar_caches):
    monkeypatch.setenv("ARMAZENAMENTO", "sheets")
    with pytest.raises(RuntimeError, match="Postgres"):
        container.pipeline()


def test_sem_fila_no_preview(monkeypatch, limpar_caches):
    monkeypatch.setenv("QSTASH_TOKEN", "x")
    monkeypatch.setenv("PROCESSAR_SEM_FILA", "true")
    assert container.fila() is None


def test_parametros_vem_das_configuracoes():
    from app.fila.controle import MemoriaRedis
    from app.servicos.configuracoes import Configuracoes

    parametros, limites = container.parametros_de(Configuracoes(None, MemoriaRedis()), 1)
    assert parametros.perguntar_valor_compra and parametros.confianca_minima == 0.7
    assert limites.max_produtos == 40
