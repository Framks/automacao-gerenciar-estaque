"""Cliente da WhatsApp Cloud API (Meta) e leitura do payload do webhook."""

import hashlib
import hmac
from dataclasses import dataclass
from typing import Protocol

import httpx

from app.speech.tts import AudioGerado


@dataclass
class MensagemRecebida:
    telefone: str  # wa_id de quem mandou (é para ele que respondemos)
    msg_id: str
    tipo: str  # "audio" ou "text"
    nome: str | None = None
    media_id: str | None = None
    texto: str | None = None


class WhatsApp(Protocol):
    def baixar_midia(self, media_id: str) -> bytes: ...
    def enviar_texto(self, para: str, texto: str) -> None: ...
    def enviar_audio(self, para: str, audio: AudioGerado) -> None: ...


def assinatura_valida(corpo: bytes, cabecalho: str | None, app_secret: str) -> bool:
    """Confere o cabeçalho X-Hub-Signature-256 enviado pela Meta."""
    if not cabecalho or not cabecalho.startswith("sha256="):
        return False
    esperado = hmac.new(app_secret.encode(), corpo, hashlib.sha256).hexdigest()
    return hmac.compare_digest(esperado, cabecalho.removeprefix("sha256="))


def extrair_mensagens(payload: dict) -> list[MensagemRecebida]:
    """Pega as mensagens de áudio e texto do webhook. Ignora status de entrega e outros tipos."""
    mensagens = []
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            valor = change.get("value", {})
            nomes = {c.get("wa_id"): c.get("profile", {}).get("name") for c in valor.get("contacts", [])}
            for msg in valor.get("messages", []):
                tipo = msg.get("type")
                base = dict(telefone=msg["from"], msg_id=msg["id"], tipo=tipo, nome=nomes.get(msg["from"]))
                if tipo == "audio":
                    mensagens.append(MensagemRecebida(**base, media_id=msg["audio"]["id"]))
                elif tipo == "text":
                    mensagens.append(MensagemRecebida(**base, texto=msg["text"]["body"]))
    return mensagens


class WhatsAppCloudClient:
    def __init__(self, token: str, phone_number_id: str, api_version: str = "v23.0"):
        self._base = f"https://graph.facebook.com/{api_version}"
        self._phone_number_id = phone_number_id
        self._http = httpx.Client(headers={"Authorization": f"Bearer {token}"}, timeout=30)

    def baixar_midia(self, media_id: str) -> bytes:
        info = self._http.get(f"{self._base}/{media_id}")
        info.raise_for_status()
        arquivo = self._http.get(info.json()["url"])
        arquivo.raise_for_status()
        return arquivo.content

    def enviar_texto(self, para: str, texto: str) -> None:
        self._enviar(para, {"type": "text", "text": {"body": texto}})

    def enviar_audio(self, para: str, audio: AudioGerado) -> None:
        upload = self._http.post(
            f"{self._base}/{self._phone_number_id}/media",
            data={"messaging_product": "whatsapp", "type": audio.mime_type},
            files={"file": (f"resposta.{audio.extensao}", audio.conteudo, audio.mime_type)},
        )
        upload.raise_for_status()
        self._enviar(para, {"type": "audio", "audio": {"id": upload.json()["id"]}})

    def _enviar(self, para: str, conteudo: dict) -> None:
        resposta = self._http.post(
            f"{self._base}/{self._phone_number_id}/messages",
            json={"messaging_product": "whatsapp", "recipient_type": "individual", "to": para, **conteudo},
        )
        resposta.raise_for_status()
