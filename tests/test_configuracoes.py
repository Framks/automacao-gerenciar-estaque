"""Configurações e flags: tipos, validação, precedência, cache e invalidação pela versão no Redis."""

import pytest

from app.db.engine import transacao
from app.db.modelos import ComercianteDB
from app.fila.controle import MemoriaRedis
from app.servicos.configuracoes import DEFINICOES, ConfiguracaoInvalida, Configuracoes


class Relogio:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


@pytest.mark.parametrize("chave, valor, esperado", [
    ("contexto_max_mensagens", "8", 8),
    ("confirmacao_confianca_minima", 0.5, 0.5),
    ("confirmacao_valor_limite", "1500.50", 1500.5),
    ("tts_voz", "pt-BR-AntonioNeural", "pt-BR-AntonioNeural"),
    ("categorias_despesa", [" aluguel ", "outros"], ["aluguel", "outros"]),
])
def test_validacao_ok(chave, valor, esperado):
    assert DEFINICOES[chave].validar(valor) == esperado


@pytest.mark.parametrize("chave, valor", [
    ("contexto_max_mensagens", "muitas"), ("contexto_max_mensagens", 2.5), ("contexto_max_mensagens", 99),
    ("confirmacao_confianca_minima", 2), ("tts_voz", "voz-que-nao-existe"), ("categorias_despesa", "aluguel"),
    ("contexto_max_mensagens", True),
])
def test_validacao_recusa(chave, valor):
    with pytest.raises(ConfiguracaoInvalida):
        DEFINICOES[chave].validar(valor)


def test_padrao_sem_banco():
    c = Configuracoes(None, MemoriaRedis())
    assert c.get("tts_voz") == "pt-BR-FranciscaNeural" and c.ligada("resposta_em_audio") is True
    with pytest.raises(KeyError):
        c.get("senha_do_banco")


def _comerciante(fabrica):
    with transacao(fabrica) as s:
        c = ComercianteDB(telefone="5588", nome="Zé")
        s.add(c)
        s.flush()
        return c.id


def test_precedencia_comerciante_global_padrao(fabrica_pg):
    cid = _comerciante(fabrica_pg)
    c = Configuracoes(fabrica_pg, MemoriaRedis())
    c.definir("tts_voz", "pt-BR-AntonioNeural", autor="admin@x")
    c.definir("tts_voz", "pt-BR-ThalitaMultilingualNeural", comerciante_id=cid)
    assert c.get("tts_voz") == "pt-BR-AntonioNeural"
    assert c.get("tts_voz", cid) == "pt-BR-ThalitaMultilingualNeural"
    assert c.get("tts_voz", 999) == "pt-BR-AntonioNeural"
    c.remover("tts_voz", cid)
    assert c.get("tts_voz", cid) == "pt-BR-AntonioNeural"
    c.remover("tts_voz")
    assert c.get("tts_voz") == "pt-BR-FranciscaNeural"


def test_global_apenas_recusa_por_comerciante(fabrica_pg):
    cid = _comerciante(fabrica_pg)
    with pytest.raises(ConfiguracaoInvalida):
        Configuracoes(fabrica_pg, MemoriaRedis()).definir("groq_model", "x", comerciante_id=cid)


def test_flags_precedencia(fabrica_pg):
    cid = _comerciante(fabrica_pg)
    c = Configuracoes(fabrica_pg, MemoriaRedis())
    c.definir_flag("resposta_em_audio", False, comerciante_id=cid)
    assert c.ligada("resposta_em_audio", cid) is False and c.ligada("resposta_em_audio") is True
    c.definir_flag("resposta_em_audio", False)
    c.definir_flag("resposta_em_audio", True, comerciante_id=cid)
    assert c.ligada("resposta_em_audio") is False and c.ligada("resposta_em_audio", cid) is True
    with pytest.raises(ConfiguracaoInvalida):
        c.definir_flag("flag_inventada", True)


def test_cache_e_invalidacao_por_versao(fabrica_pg):
    redis, relogio = MemoriaRedis(), Relogio()
    leitor = Configuracoes(fabrica_pg, redis, relogio=relogio)  # outro processo (outra função serverless)
    escritor = Configuracoes(fabrica_pg, redis)
    assert leitor.get("contexto_max_mensagens") == 6
    escritor.definir("contexto_max_mensagens", 10)
    relogio.t = 1  # dentro dos 5 s: ainda não olhou a versão
    assert leitor.get("contexto_max_mensagens") == 6
    relogio.t = 6  # olhou a versão no Redis, mudou: recarrega antes dos 60 s
    assert leitor.get("contexto_max_mensagens") == 10


def test_ttl_sem_redis_recarrega_em_60s(fabrica_pg):
    class RedisFora:
        def get(self, k):
            raise ConnectionError

        def incr(self, k):
            raise ConnectionError

    relogio = Relogio()
    leitor = Configuracoes(fabrica_pg, RedisFora(), relogio=relogio)
    assert leitor.get("pendencia_validade_horas") == 24
    Configuracoes(fabrica_pg, RedisFora()).definir("pendencia_validade_horas", 12)
    relogio.t = 30
    assert leitor.get("pendencia_validade_horas") == 24
    relogio.t = 61
    assert leitor.get("pendencia_validade_horas") == 12


def test_banco_fora_usa_padrao():
    class FabricaQuebrada:
        def __call__(self):
            raise RuntimeError("banco fora")

    c = Configuracoes(FabricaQuebrada(), MemoriaRedis())
    assert c.get("contexto_max_produtos") == 40


def test_listar_para_o_admin(fabrica_pg):
    cid = _comerciante(fabrica_pg)
    c = Configuracoes(fabrica_pg, MemoriaRedis())
    c.definir("tts_voz", "pt-BR-AntonioNeural", comerciante_id=cid)
    tudo = c.listar()
    voz = next(x for x in tudo["configuracoes"] if x["chave"] == "tts_voz")
    assert voz["comerciantes"] == {str(cid): "pt-BR-AntonioNeural"} and voz["global"] is None
    assert tudo["versao"] == "1"
