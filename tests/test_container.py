"""O container monta o pipeline real (sem rede) nas duas formas de armazenamento."""

import pytest

from app import container
from app.config import get_settings
from app.storage.pg import PgComerciantes, PgInventoryRepository

@pytest.fixture
def limpar_caches(monkeypatch):
    for nome in ("ARMAZENAMENTO", "BOT_DATABASE_URL", "DATABASE_URL", "UPSTASH_REDIS_REST_URL", "QSTASH_TOKEN"):
        monkeypatch.delenv(nome, raising=False)
    monkeypatch.setattr("app.config.Settings.model_config", {**get_settings().model_config, "env_file": None})

    def limpar():
        for f in (get_settings, container.redis, container.controle, container.fila, container.pipeline):
            f.cache_clear()

    limpar()
    yield
    limpar()

def test_pipeline_com_postgres(monkeypatch, limpar_caches):
    monkeypatch.setenv("ARMAZENAMENTO", "postgres")
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@localhost:1/x")
    p = container.pipeline()
    assert isinstance(p._tenants, PgComerciantes)
    assert isinstance(p._repositorio(type("C", (), {"id": 1, "telefone": "1"})()), PgInventoryRepository)

def test_pipeline_com_sheets(monkeypatch, limpar_caches):
    monkeypatch.setenv("ARMAZENAMENTO", "sheets")
    monkeypatch.setattr("app.google_auth.servico_sheets", lambda s: object())
    p = container.pipeline()
    assert type(p._tenants).__name__ == "TenantStore"

def test_sem_fila_no_preview(monkeypatch, limpar_caches):
    monkeypatch.setenv("QSTASH_TOKEN", "x")
    monkeypatch.setenv("PROCESSAR_SEM_FILA", "true")
    assert container.fila() is None
