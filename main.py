"""Entrada HTTP (FastAPI). A Vercel detecta este arquivo (main.py na raiz) e manda todas as requisições para `app`.

- GET  /api/webhook : verificação do webhook pela Meta
- POST /api/webhook : recebe mensagens, valida, descarta duplicadas e enfileira (responde 200 rápido)
- POST /api/worker  : chamado pelo QStash; roda o pipeline completo
- POST /api/tarefas/limpeza : agendado no QStash; expira pendências e aplica a retenção das mensagens
"""

import logging
import sys
from dataclasses import asdict
from pathlib import Path

from fastapi import FastAPI, Request, Response

sys.path.insert(0, str(Path(__file__).resolve().parent))  # garante "import app" a partir da raiz

from app import container  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.errors import ServicoIndisponivel  # noqa: E402
from app.whatsapp.client import (  # noqa: E402
    MensagemRecebida,
    assinatura_valida,
    extrair_mensagens,
    extrair_status,
    mascarar,
    telefone_permitido,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("api")

app = FastAPI()


OBRIGATORIAS = [
    "whatsapp_token", "whatsapp_phone_number_id", "whatsapp_app_secret", "whatsapp_verify_token",
    "groq_api_key", "upstash_redis_rest_url", "upstash_redis_rest_token",
    "qstash_token", "qstash_current_signing_key", "qstash_next_signing_key", "base_url",
]
OBRIGATORIAS_POR_ARMAZENAMENTO = {
    "sheets": ["google_service_account_json", "spreadsheet_id"],
    "postgres": ["banco_url"],
}
NOMES_VARIAVEIS = {"base_url": "PUBLIC_BASE_URL", "banco_url": "DATABASE_URL"}


@app.get("/api/health")
def health():
    """Diagnóstico sem segredos: só nomes de variáveis ausentes e URLs públicas."""
    s = get_settings()
    obrigatorias = OBRIGATORIAS + OBRIGATORIAS_POR_ARMAZENAMENTO.get(s.armazenamento, [])
    faltando = [NOMES_VARIAVEIS.get(nome, nome.upper()) for nome in obrigatorias if not getattr(s, nome)]
    banco = None
    if s.banco_url:
        from app.db.engine import banco_ok

        banco = "ok" if banco_ok(s.banco_url) else "erro"
    return {
        "ok": True,
        "ambiente": s.vercel_env or "local",
        "commit": s.vercel_git_commit_sha[:7],
        "variaveis_faltando": faltando,
        "armazenamento": s.armazenamento,
        "banco": banco or "não configurado",
        "worker_url": s.worker_url,
        "qstash_url": s.qstash_url or "(padrão)",
        "groq_model": s.groq_model,
        "groq_stt_model": s.groq_stt_model,
        "telefones_permitidos": len([t for t in s.telefones_permitidos.split(",") if t.strip()]) or "todos",
    }


@app.get("/api/webhook")
def verificar_webhook(request: Request):
    p = request.query_params
    if p.get("hub.mode") == "subscribe" and p.get("hub.verify_token") == get_settings().whatsapp_verify_token:
        return Response(content=p.get("hub.challenge", ""), media_type="text/plain")
    return Response(status_code=403)


@app.post("/api/webhook")
async def receber_webhook(request: Request):
    corpo = await request.body()
    settings = get_settings()
    if not assinatura_valida(corpo, request.headers.get("X-Hub-Signature-256"), settings.whatsapp_app_secret):
        log.warning("assinatura do webhook inválida")
        return Response(status_code=401)

    payload = await request.json()
    controle, fila = container.controle(), container.fila()

    for evento in extrair_status(payload):
        if evento["erros"]:
            log.error("entrega FALHOU: %s", evento)
        else:
            log.info("entrega: %s para %s", evento["status"], evento["para"])
        try:
            controle.registrar_status(evento)
            container.mensagens().atualizar_entrega(evento["wa_msg_id"], evento["status"], evento["erros"])
        except Exception:
            log.exception("não consegui guardar o status de entrega")

    for msg in extrair_mensagens(payload):
        if not telefone_permitido(msg.telefone, settings.telefones_permitidos):
            log.info("ignorando número fora da lista de permitidos: %s", mascarar(msg.telefone))
            _registrar_ignorada(msg)
            continue
        if not controle.primeira_vez(msg.msg_id):
            continue
        if fila:
            try:
                fila.publicar(asdict(msg))
            except Exception as e:
                log.exception("falha ao enfileirar %s", msg.msg_id)
                controle.esquecer(msg.msg_id)
                # a Meta reenvia o webhook; o corpo (só visível a quem tem assinatura válida) ajuda no diagnóstico
                return Response(status_code=500, content=f"falha ao enfileirar: {type(e).__name__}: {str(e)[:300]}")
        else:  # sem QStash (desenvolvimento): processa na hora
            try:
                container.pipeline().processar(msg)
            except ServicoIndisponivel:
                log.exception("serviço indisponível processando %s", msg.msg_id)
    return {"ok": True}


def _registrar_ignorada(msg: MensagemRecebida) -> None:
    """Fica no banco que chegou algo de um número não permitido: só o telefone mascarado e a hora."""
    try:
        mensagens = container.mensagens()
        mid, _ = mensagens.registrar_entrada(None, msg.msg_id, msg.tipo, "", mascarar(msg.telefone))
        mensagens.atualizar(mid, status="ignorada")
    except Exception:
        log.exception("não consegui registrar a mensagem ignorada")


@app.post("/api/worker")
async def worker(request: Request):
    corpo = (await request.body()).decode()
    settings = get_settings()
    if not container.verificador_qstash().valido(corpo, request.headers.get("Upstash-Signature"), settings.worker_url):
        return Response(status_code=401)

    msg = MensagemRecebida(**(await request.json()))
    try:
        container.pipeline().processar(msg)
    except ServicoIndisponivel as e:
        log.warning("tentar de novo depois: %s", e)
        return Response(status_code=503)  # QStash tenta de novo
    return {"ok": True}


@app.post("/api/tarefas/limpeza")
async def limpeza(request: Request):
    """Agendada no QStash (assinada como o worker): expira pendências vencidas e apaga mensagens antigas."""
    corpo = (await request.body()).decode()
    settings = get_settings()
    url = settings.base_url + "/api/tarefas/limpeza"
    if not container.verificador_qstash().valido(corpo, request.headers.get("Upstash-Signature"), url):
        return Response(status_code=401)
    mensagens = container.mensagens()
    expiradas = mensagens.expirar_pendencias()
    apagadas = mensagens.limpar(settings.retencao_mensagens_dias)
    log.info("limpeza: %d pendências expiradas, %d mensagens apagadas", expiradas, apagadas)
    return {"ok": True, "pendencias_expiradas": expiradas, "mensagens_apagadas": apagadas}
