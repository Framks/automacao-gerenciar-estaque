"""Entrada HTTP (FastAPI) publicada como função Python na Vercel.

- GET  /api/webhook : verificação do webhook pela Meta
- POST /api/webhook : recebe mensagens, valida, descarta duplicadas e enfileira (responde 200 rápido)
- POST /api/worker  : chamado pelo QStash; roda o pipeline completo
"""

import logging
import sys
from dataclasses import asdict
from pathlib import Path

from fastapi import FastAPI, Request, Response

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # permite "import app" na Vercel

from app import container  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.errors import ServicoIndisponivel  # noqa: E402
from app.whatsapp.client import MensagemRecebida, assinatura_valida, extrair_mensagens  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("api")

app = FastAPI()


@app.get("/api/health")
def health():
    return {"ok": True}


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

    controle, fila = container.controle(), container.fila()
    for msg in extrair_mensagens(await request.json()):
        if not controle.primeira_vez(msg.msg_id):
            continue
        if fila:
            try:
                fila.publicar(asdict(msg))
            except Exception:
                log.exception("falha ao enfileirar %s", msg.msg_id)
                controle.esquecer(msg.msg_id)
                return Response(status_code=500)  # a Meta reenvia o webhook
        else:  # sem QStash (desenvolvimento): processa na hora
            try:
                container.pipeline().processar(msg)
            except ServicoIndisponivel:
                log.exception("serviço indisponível processando %s", msg.msg_id)
    return {"ok": True}


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
