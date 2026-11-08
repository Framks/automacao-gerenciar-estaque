"""Monta as dependências reais a partir da configuração (um único lugar para trocar implementações)."""

from functools import lru_cache

from app.config import get_settings
from app.fila.controle import Controle, MemoriaRedis, RedisComPrefixo
from app.fila.qstash import Fila, VerificadorQStash
from app.llm.interpreter import GroqInterpreter
from app.pipeline import Pipeline
from app.speech.stt import GroqWhisperSTT
from app.speech.tts import EdgeTTS
from app.whatsapp.client import WhatsAppCloudClient


@lru_cache
def redis():
    s = get_settings()
    if not s.upstash_redis_rest_url:
        return MemoriaRedis()  # desenvolvimento local
    from upstash_redis import Redis

    cliente = Redis(url=s.upstash_redis_rest_url, token=s.upstash_redis_rest_token)
    return RedisComPrefixo(cliente, s.redis_prefixo) if s.redis_prefixo else cliente


@lru_cache
def controle() -> Controle:
    return Controle(redis())


@lru_cache
def fila() -> Fila | None:
    s = get_settings()
    if not s.qstash_token or s.processar_sem_fila:
        return None
    return Fila(s.qstash_token, s.worker_url, s.qstash_url)


@lru_cache
def verificador_qstash() -> VerificadorQStash:
    s = get_settings()
    return VerificadorQStash(s.qstash_current_signing_key, s.qstash_next_signing_key)


def _fabrica():
    from app.db.engine import fabrica

    s = get_settings()
    if s.armazenamento != "postgres" or not s.banco_url:
        raise RuntimeError("a conversa (F2) precisa do Postgres: ARMAZENAMENTO=postgres e DATABASE_URL")
    return fabrica(s.banco_url)


@lru_cache
def comerciantes():
    from app.storage.pg import PgComerciantes

    s = get_settings()
    return PgComerciantes(_fabrica(), redis(), s.timezone)


@lru_cache
def mensagens():
    from app.storage.pg_conversa import PgMensagens

    return PgMensagens(_fabrica(), get_settings().timezone)


@lru_cache
def configuracoes():
    from app.servicos.configuracoes import Configuracoes

    return Configuracoes(_fabrica(), redis())


@lru_cache
def acesso():
    from app.servicos.comerciantes import Acesso

    return Acesso(_fabrica(), redis())


def parametros_de(cfg, comerciante_id):
    """Parâmetros da conversa lidos das configurações e flags (valem por mensagem, sem redeploy)."""
    from decimal import Decimal

    from app.conversa.contexto import LimitesContexto
    from app.conversa.modelos import Parametros

    parametros = Parametros(
        pendencias_ativas=cfg.ligada("pendencias_ativas", comerciante_id),
        perguntar_valor_compra=cfg.ligada("perguntar_valor_compra", comerciante_id),
        confirmacao_ambiguidade=cfg.ligada("confirmacao_ambiguidade", comerciante_id),
        despesas=cfg.ligada("despesas", comerciante_id),
        confianca_minima=float(cfg.get("confirmacao_confianca_minima", comerciante_id)),
        valor_limite=Decimal(str(cfg.get("confirmacao_valor_limite", comerciante_id))),
        pendencia_validade_horas=int(cfg.get("pendencia_validade_horas", comerciante_id)),
        categorias_despesa=tuple(cfg.get("categorias_despesa", comerciante_id)),
        fiado=cfg.ligada("fiado", comerciante_id),
        perguntar_pagamento=cfg.ligada("perguntar_pagamento", comerciante_id),
        pagamento_padrao=cfg.get("pagamento_padrao", comerciante_id),
        formas_pagamento=tuple(cfg.get("formas_pagamento", comerciante_id)),
        fechamento_horario=cfg.get("fechamento_diario_horario", comerciante_id),
        fechamento_mensal=bool(cfg.get("fechamento_mensal", comerciante_id)),
        fechamento_formato=cfg.get("fechamento_formato", comerciante_id),
    )
    limites = LimitesContexto(
        max_mensagens=int(cfg.get("contexto_max_mensagens", comerciante_id)),
        janela_horas=int(cfg.get("contexto_janela_horas", comerciante_id)),
        max_produtos=int(cfg.get("contexto_max_produtos", comerciante_id)),
        max_clientes=int(cfg.get("contexto_max_clientes", comerciante_id)),
    )
    return parametros, limites


@lru_cache
def pipeline() -> Pipeline:
    from app.storage.pg_conversa import PgLoja

    s = get_settings()
    f = _fabrica()
    cfg = configuracoes()
    return Pipeline(
        whatsapp=WhatsAppCloudClient(s.whatsapp_token, s.whatsapp_phone_number_id, s.graph_api_version),
        stt=GroqWhisperSTT(s.groq_api_key, lambda: cfg.get("groq_stt_model")),
        interpreter=GroqInterpreter(s.groq_api_key, lambda: cfg.get("groq_model")),
        tts=EdgeTTS(s.tts_voice),
        tenants=comerciantes(),
        loja=lambda c: PgLoja(f, c.id, fuso_de(c.id)),
        mensagens=mensagens(),
        controle=controle(),
        timezone=s.timezone,
        parametros=lambda c: parametros_de(cfg, c.id)[0],
        limites=lambda c: parametros_de(cfg, c.id)[1],
        resposta_em_audio=lambda c: cfg.ligada("resposta_em_audio", c.id),
        tts_de=lambda c: EdgeTTS(cfg.get("tts_voz", c.id), cfg.get("tts_velocidade", c.id)),
        relatorios=lambda c: relatorios_de(c.id),
        configurar=lambda c, valores: configurar_pelo_comerciante(cfg, c.id, valores),
        aviso=lambda c: fechamento().aviso_pendente(c.id),
    )


def fuso_de(comerciante_id: int) -> str:
    try:
        return configuracoes().get("fuso_horario", comerciante_id)
    except Exception:
        return get_settings().timezone


def relatorios_de(comerciante_id: int):
    from app.servicos.graficos import desenhar
    from app.servicos.relatorios import ServicoRelatorios
    from app.storage.pg_conversa import PgLoja

    fuso = fuso_de(comerciante_id)
    return ServicoRelatorios(PgLoja(_fabrica(), comerciante_id, fuso), fuso, grafico=desenhar)


def configurar_pelo_comerciante(cfg, comerciante_id: int, valores: dict) -> None:
    """Grava as preferências que o comerciante mudou pela conversa (só as chaves permitidas)."""
    from app.servicos.configuracoes import CONFIG_DO_COMERCIANTE

    for chave, valor in valores.items():
        if chave in CONFIG_DO_COMERCIANTE:
            cfg.definir(chave, valor, comerciante_id, autor="whatsapp")


@lru_cache
def fechamento():
    from app.servicos.fechamento import Fechamento

    s = get_settings()
    return Fechamento(
        fabrica=_fabrica(), configuracoes=configuracoes(),
        whatsapp=WhatsAppCloudClient(s.whatsapp_token, s.whatsapp_phone_number_id, s.graph_api_version),
        mensagens=mensagens(), relatorios=relatorios_de, fila=fila_tarefas(), fuso_padrao=s.timezone,
    )


@lru_cache
def fila_tarefas():
    """Fan-out do fechamento: uma mensagem do QStash por comerciante (None = processa na hora)."""
    from app.fila.qstash import FilaTarefas

    s = get_settings()
    if not s.qstash_token or s.processar_sem_fila:
        return None
    return FilaTarefas(s.qstash_token, s.base_url + "/api/tarefas/fechamento/enviar", s.qstash_url)
