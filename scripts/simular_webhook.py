"""Simula a Meta chamando o webhook: monta o payload, assina com WHATSAPP_APP_SECRET e envia.

Serve para testar um deploy de preview sem mexer no webhook da Meta (que aponta para a produção).
A resposta do bot chega de verdade no WhatsApp do --telefone.

Uso:
  python scripts/simular_webhook.py --url https://<preview>.vercel.app --texto "comprei 2 kg de arroz"
  python scripts/simular_webhook.py --url ... --falar "comprei 2 quilos de arroz a 5 reais o quilo"
  python scripts/simular_webhook.py --url ... --audio meu_audio.ogg

--falar gera o áudio com a mesma voz do bot (edge-tts) e --audio usa um arquivo; nos dois casos o áudio
é enviado para a Meta (upload de mídia), e o webhook recebe o media_id, como numa mensagem de voz real.

Variáveis (lidas do .env): WHATSAPP_APP_SECRET, WHATSAPP_TOKEN, WHATSAPP_PHONE_NUMBER_ID,
VERCEL_PROTECTION_BYPASS (passa pela proteção dos deploys de preview), TELEFONE_TESTE (padrão do --telefone).
"""

import argparse
import hashlib
import hmac
import json
import os
import sys
import time
import uuid
from pathlib import Path

import httpx
from dotenv import load_dotenv

load_dotenv()  # TELEFONE_TESTE e VERCEL_PROTECTION_BYPASS vêm do .env local
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings  # noqa: E402


def payload(telefone: str, nome: str, msg: dict) -> dict:
    return {
        "object": "whatsapp_business_account",
        "entry": [{
            "id": "SIMULADO",
            "changes": [{
                "field": "messages",
                "value": {
                    "messaging_product": "whatsapp",
                    "contacts": [{"profile": {"name": nome}, "wa_id": telefone}],
                    "messages": [{"from": telefone, "id": f"wamid.SIM{uuid.uuid4().hex[:20]}",
                                  "timestamp": str(int(time.time())), **msg}],
                },
            }],
        }],
    }


def assinar(corpo: bytes, segredo: str) -> str:
    return "sha256=" + hmac.new(segredo.encode(), corpo, hashlib.sha256).hexdigest()


def subir_audio(conteudo: bytes, mime: str) -> str:
    s = get_settings()
    resposta = httpx.post(
        f"https://graph.facebook.com/{s.graph_api_version}/{s.whatsapp_phone_number_id}/media",
        headers={"Authorization": f"Bearer {s.whatsapp_token}"},
        data={"messaging_product": "whatsapp", "type": mime},
        files={"file": ("audio." + ("ogg" if "ogg" in mime else "mp3"), conteudo, mime)},
        timeout=60,
    )
    resposta.raise_for_status()
    return resposta.json()["id"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True, help="base do deploy, ex.: https://xxx.vercel.app")
    ap.add_argument("--telefone", default=os.environ.get("TELEFONE_TESTE", ""))
    ap.add_argument("--nome", default="Teste")
    grupo = ap.add_mutually_exclusive_group(required=True)
    grupo.add_argument("--texto")
    grupo.add_argument("--falar", help="texto que vira áudio (voz do bot) antes de enviar")
    grupo.add_argument("--audio", help="arquivo .ogg/.mp3")
    args = ap.parse_args()

    s = get_settings()
    telefone = args.telefone
    if not telefone:
        ap.error("informe --telefone (ou TELEFONE_TESTE no .env)")

    if args.texto:
        msg = {"type": "text", "text": {"body": args.texto}}
    else:
        if args.falar:
            from app.speech.tts import EdgeTTS

            gerado = EdgeTTS(s.tts_voice).sintetizar(args.falar)
            conteudo, mime = gerado.conteudo, gerado.mime_type
        else:
            conteudo = Path(args.audio).read_bytes()
            mime = "audio/ogg" if args.audio.endswith(".ogg") else "audio/mpeg"
        media_id = subir_audio(conteudo, mime)
        msg = {"type": "audio", "audio": {"id": media_id, "mime_type": mime, "voice": True}}

    corpo = json.dumps(payload(telefone, args.nome, msg)).encode()
    cabecalhos = {"Content-Type": "application/json", "X-Hub-Signature-256": assinar(corpo, s.whatsapp_app_secret)}
    bypass = os.environ.get("VERCEL_PROTECTION_BYPASS")
    if bypass:
        cabecalhos["x-vercel-protection-bypass"] = bypass

    inicio = time.perf_counter()
    resposta = httpx.post(args.url.rstrip("/") + "/api/webhook", content=corpo, headers=cabecalhos, timeout=90)
    print(f"HTTP {resposta.status_code} em {time.perf_counter() - inicio:.1f}s: {resposta.text[:300]}")
    return 0 if resposta.is_success else 1


if __name__ == "__main__":
    raise SystemExit(main())
