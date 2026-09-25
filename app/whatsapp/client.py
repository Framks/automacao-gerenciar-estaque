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


class ErroWhatsApp(RuntimeError):
    """Erro devolvido pela API da Meta, com o código e a mensagem dela (ex.: 131030)."""


def numero_para_envio(telefone: str) -> str:
    """Celular do Brasil que chega sem o 9 (55 + DDD + 8 dígitos) ganha o 9 na hora de enviar.

    O webhook costuma mandar o wa_id brasileiro sem o nono dígito, mas a lista de destinatários
    de teste da Meta (e alguns envios) exigem o número com 13 dígitos. O cadastro continua usando
    o wa_id original; só o envio é ajustado.
    """
    if telefone.startswith("55") and len(telefone) == 12 and telefone[4] in "6789":
        return telefone[:4] + "9" + telefone[4:]
    return telefone


def _checar(resposta: httpx.Response, etapa: str) -> None:
    if resposta.is_success:
        return
    try:
        erro = resposta.json().get("error", {})
        detalhe = f"{erro.get('code')}: {erro.get('message')}"
        if erro.get("error_data", {}).get("details"):
            detalhe += f" | {erro['error_data']['details']}"
    except ValueError:
        detalhe = resposta.text[:300]
    raise ErroWhatsApp(f"{etapa}: HTTP {resposta.status_code} - {detalhe}")


class WhatsAppCloudClient:
    def __init__(self, token: str, phone_number_id: str, api_version: str = "v23.0"):
        self._base = f"https://graph.facebook.com/{api_version}"
        self._phone_number_id = phone_number_id
        self._http = httpx.Client(headers={"Authorization": f"Bearer {token}"}, timeout=30)

    def baixar_midia(self, media_id: str) -> bytes:
        info = self._http.get(f"{self._base}/{media_id}")
        _checar(info, "buscar mídia")
        arquivo = self._http.get(info.json()["url"])
        _checar(arquivo, "baixar mídia")
        return arquivo.content

    def enviar_texto(self, para: str, texto: str) -> None:
        self._enviar(para, {"type": "text", "text": {"body": texto}})

    def enviar_audio(self, para: str, audio: AudioGerado) -> None:
        upload = self._http.post(
            f"{self._base}/{self._phone_number_id}/media",
            data={"messaging_product": "whatsapp", "type": audio.mime_type},
            files={"file": (f"resposta.{audio.extensao}", audio.conteudo, audio.mime_type)},
        )
        _checar(upload, "subir áudio")
        self._enviar(para, {"type": "audio", "audio": {"id": upload.json()["id"]}})

    def _enviar(self, para: str, conteudo: dict) -> None:
        resposta = self._http.post(
            f"{self._base}/{self._phone_number_id}/messages",
            json={
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "to": numero_para_envio(para),
                **conteudo,
            },
        )
        _checar(resposta, f"enviar {conteudo['type']}")
