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
    # Só estes números são atendidos (separados por vírgula, com DDI; com ou sem o nono dígito).
    # Vazio = atende qualquer número.
    telefones_permitidos: str = ""

    # Groq (transcrição + interpretação)
    groq_api_key: str = ""
    groq_model: str = "openai/gpt-oss-120b"
    groq_stt_model: str = "whisper-large-v3-turbo"

    # Google Sheets (conta de serviço)
    google_service_account_json: str = ""  # conteúdo do JSON da chave, numa linha
    spreadsheet_id: str = ""  # planilha compartilhada com o client_email da conta de serviço (Editor)

    # Postgres (Neon). A integração da Vercel injeta DATABASE_URL (a mesma em produção e preview);
    # BOT_DATABASE_URL, se definida, tem prioridade: é como o preview aponta para o banco dele.
    database_url: str = ""
    bot_database_url: str = ""
    # Onde ficam estoque e cadastro: "sheets" (Google Sheets) ou "postgres".
    armazenamento: str = "sheets"
    retencao_mensagens_dias: int = 90  # na F3-01 vira configuração no banco

    # Painel admin (F3-02): segredo do JWT e a origem do front (CORS), ex.: https://admin-bot.vercel.app
    admin_jwt_secret: str = ""
    admin_origin: str = ""

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

    # Isolamento do preview (variáveis só do ambiente Preview na Vercel):
    # - prefixo nas chaves do Redis, para não misturar dedupe/cache com a produção;
    # - processar na hora, sem fila: o QStash chamaria o worker da produção (PUBLIC_BASE_URL).
    redis_prefixo: str = ""
    processar_sem_fila: bool = False
    # Testes sem gastar o Groq: mensagens de texto "#ops {json}" viram operações direto. Nunca vale em produção.
    interpretador_teste: bool = False

    @property
    def modo_teste(self) -> bool:
        return self.interpretador_teste and self.vercel_env != "production"

    # URL pública do deploy (ex.: https://meu-bot.vercel.app), usada para o QStash chamar o worker.
    # Se vazia, usa o domínio de produção que a própria Vercel informa (VERCEL_PROJECT_PRODUCTION_URL).
    public_base_url: str = ""
    vercel_project_production_url: str = ""  # variável de sistema da Vercel, sem "https://"
    vercel_env: str = ""  # production | preview | development (sistema da Vercel)
    vercel_git_commit_sha: str = ""  # commit do deploy (sistema da Vercel)

    @property
    def base_url(self) -> str:
        if self.public_base_url:
            return self.public_base_url.rstrip("/")
        if self.vercel_project_production_url:
            return "https://" + self.vercel_project_production_url.strip("/")
        return ""

    @property
    def banco_url(self) -> str:
        return self.bot_database_url or self.database_url

    @property
    def worker_url(self) -> str:
        return self.base_url + "/api/worker"


@lru_cache
def get_settings() -> Settings:
    return Settings()
