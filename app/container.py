"""Monta as dependências reais a partir da configuração (um único lugar para trocar implementações)."""

from functools import lru_cache

from app.config import get_settings
from app.fila.controle import Controle, MemoriaRedis
from app.fila.qstash import Fila, VerificadorQStash
from app.google_auth import servico_sheets
from app.llm.interpreter import GroqInterpreter
from app.pipeline import Pipeline
from app.speech.stt import GroqWhisperSTT
from app.speech.tts import EdgeTTS
from app.storage.sheets_repo import SheetsInventoryRepository
from app.storage.tenants import TenantStore
from app.whatsapp.client import WhatsAppCloudClient


@lru_cache
def redis():
    s = get_settings()
    if not s.upstash_redis_rest_url:
        return MemoriaRedis()  # desenvolvimento local
    from upstash_redis import Redis

    return Redis(url=s.upstash_redis_rest_url, token=s.upstash_redis_rest_token)


@lru_cache
def controle() -> Controle:
    return Controle(redis())


@lru_cache
def fila() -> Fila | None:
    s = get_settings()
    return Fila(s.qstash_token, s.worker_url, s.qstash_url) if s.qstash_token else None


@lru_cache
def verificador_qstash() -> VerificadorQStash:
    s = get_settings()
    return VerificadorQStash(s.qstash_current_signing_key, s.qstash_next_signing_key)


@lru_cache
def pipeline() -> Pipeline:
    s = get_settings()
    sheets = servico_sheets(s)
    return Pipeline(
        whatsapp=WhatsAppCloudClient(s.whatsapp_token, s.whatsapp_phone_number_id, s.graph_api_version),
        stt=GroqWhisperSTT(s.groq_api_key, s.groq_stt_model),
        interpreter=GroqInterpreter(s.groq_api_key, s.groq_model),
        tts=EdgeTTS(s.tts_voice),
        tenants=TenantStore(sheets, s.spreadsheet_id, redis()),
        repositorio=lambda telefone: SheetsInventoryRepository(sheets, s.spreadsheet_id, telefone),
        controle=controle(),
        timezone=s.timezone,
    )
