"""Configuração lida de variáveis de ambiente (ou de um arquivo .env local)."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # str_strip_whitespace: tolera espaços colados sem querer (ex.: "PUBLIC_BASE_URL= https://...")
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", str_strip_whitespace=True
    )

    # WhatsApp Cloud API (Meta)
    whatsapp_token: str = ""
    whatsapp_phone_number_id: str = ""
    whatsapp_app_secret: str = ""
    whatsapp_verify_token: str = ""
    graph_api_version: str = "v23.0"

    # Groq (transcrição + interpretação)
    groq_api_key: str = ""
    groq_model: str = "llama-3.3-70b-versatile"
    groq_stt_model: str = "whisper-large-v3-turbo"

    # Google Sheets (conta de serviço)
    google_service_account_json: str = ""  # conteúdo do JSON da chave, numa linha
    spreadsheet_id: str = ""  # planilha compartilhada com o client_email da conta de serviço (Editor)

    # Voz e fuso
    tts_voice: str = "pt-BR-FranciscaNeural"  # edge-tts; outras: pt-BR-AntonioNeural, pt-BR-ThalitaMultilingualNeural
    timezone: str = "America/Fortaleza"

    # Upstash
    upstash_redis_rest_url: str = ""
    upstash_redis_rest_token: str = ""
    qstash_url: str = ""  # endereço regional do QStash (ex.: https://qstash-eu-central-1.upstash.io)
    qstash_token: str = ""  # vazio = processa na hora, sem fila (útil em desenvolvimento local)
    qstash_current_signing_key: str = ""
    qstash_next_signing_key: str = ""

    # URL pública do deploy (ex.: https://meu-bot.vercel.app), usada para o QStash chamar o worker
    public_base_url: str = ""

    @property
    def worker_url(self) -> str:
        return self.public_base_url.rstrip("/") + "/api/worker"


@lru_cache
def get_settings() -> Settings:
    return Settings()
