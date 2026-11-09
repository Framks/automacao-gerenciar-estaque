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
    payload: str | None = None  # botão de resposta rápida de um template (ex.: "Ver detalhes" do fechamento)


class WhatsApp(Protocol):
    def baixar_midia(self, media_id: str) -> bytes: ...
    def enviar_texto(self, para: str, texto: str) -> str: ...  # devolve o wa_msg_id da mensagem enviada
    def enviar_audio(self, para: str, audio: AudioGerado) -> str: ...
    def enviar_imagem(self, para: str, conteudo: bytes, mime: str, legenda: str = "") -> str: ...
    def enviar_documento(self, para: str, conteudo: bytes, nome: str, mime: str, legenda: str = "") -> str: ...
    def enviar_template(self, para: str, nome: str, idioma: str, parametros: list[str],
                        payload_botao: str | None = None) -> str: ...


def assinatura_valida(corpo: bytes, cabecalho: str | None, app_secret: str) -> bool:
    """Confere o cabeçalho X-Hub-Signature-256 enviado pela Meta."""
    if not cabecalho or not cabecalho.startswith("sha256="):
        return False
    esperado = hmac.new(app_secret.encode(), corpo, hashlib.sha256).hexdigest()
    return hmac.compare_digest(esperado, cabecalho.removeprefix("sha256="))


def extrair_status(payload: dict) -> list[dict]:
    """Eventos de entrega das mensagens que o bot enviou (sent, delivered, read, failed)."""
    eventos = []
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            for st in change.get("value", {}).get("statuses", []):
                erros = [
                    {"code": e.get("code"), "title": e.get("title"), "message": e.get("message"),
                     "details": (e.get("error_data") or {}).get("details")}
                    for e in st.get("errors", [])
                ]
                eventos.append({
                    "status": st.get("status"),
                    "para": mascarar(st.get("recipient_id", "")),
                    "msg_id": st.get("id", "")[-12:],
                    "wa_msg_id": st.get("id", ""),
                    "quando": st.get("timestamp"),
                    "erros": erros,
                })
    return eventos


def mascarar(telefone: str) -> str:
    return telefone[:4] + "****" + telefone[-4:] if len(telefone) > 8 else telefone


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
                elif tipo == "button":  # resposta rápida de template
                    botao = msg.get("button", {})
                    mensagens.append(MensagemRecebida(**base, texto=botao.get("text", ""),
                                                      payload=botao.get("payload") or None))
                elif tipo == "interactive" and msg.get("interactive", {}).get("type") == "button_reply":
                    botao = msg["interactive"]["button_reply"]
                    mensagens.append(MensagemRecebida(**{**base, "tipo": "button"}, texto=botao.get("title", ""),
                                                      payload=botao.get("id") or None))
    return mensagens


class ErroWhatsApp(RuntimeError):
    """Erro devolvido pela API da Meta, com o código e a mensagem dela (ex.: 131030)."""

    def __init__(self, mensagem: str, codigo: int | None = None):
        super().__init__(mensagem)
        self.codigo = codigo


FORA_DA_JANELA = 131047  # mais de 24h desde a última mensagem do cliente: só template


def numero_para_envio(telefone: str) -> str:
    """Celular do Brasil que chega sem o 9 (55 + DDD + 8 dígitos) ganha o 9 na hora de enviar.

    O webhook costuma mandar o wa_id brasileiro sem o nono dígito, mas a lista de destinatários
    de teste da Meta (e alguns envios) exigem o número com 13 dígitos. O cadastro continua usando
    o wa_id original; só o envio é ajustado.
    """
    if telefone.startswith("55") and len(telefone) == 12 and telefone[4] in "6789":
        return telefone[:4] + "9" + telefone[4:]
    return telefone


def wa_id_brasil(telefone: str) -> str:
    """Forma em que a Meta manda o wa_id de celular brasileiro: sem o nono dígito (55 + DDD + 8 dígitos)."""
    t = "".join(c for c in telefone if c.isdigit())
    if t.startswith("55") and len(t) == 13 and t[4] == "9":
        return t[:4] + t[5:]
    return t


def variantes_telefone(telefone: str) -> set[str]:
    """O mesmo número com e sem o nono dígito (para achar o cadastro de qualquer jeito que vier)."""
    return {telefone, wa_id_brasil(telefone), numero_para_envio(wa_id_brasil(telefone))}


def telefone_permitido(telefone: str, permitidos: str) -> bool:
    """True se a lista estiver vazia ou contiver o telefone (compara já com o nono dígito)."""
    lista = {numero_para_envio(t.strip().lstrip("+")) for t in permitidos.split(",") if t.strip()}
    return not lista or numero_para_envio(telefone) in lista


def _checar(resposta: httpx.Response, etapa: str) -> None:
    if resposta.is_success:
        return
    codigo = None
    try:
        erro = resposta.json().get("error", {})
        codigo = erro.get("code")
        detalhe = f"{erro.get('code')}: {erro.get('message')}"
        if erro.get("error_data", {}).get("details"):
            detalhe += f" | {erro['error_data']['details']}"
    except ValueError:
        detalhe = resposta.text[:300]
    raise ErroWhatsApp(f"{etapa}: HTTP {resposta.status_code} - {detalhe}", codigo)


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

    def enviar_texto(self, para: str, texto: str) -> str:
        return self._enviar(para, {"type": "text", "text": {"body": texto}})

    def enviar_audio(self, para: str, audio: AudioGerado) -> str:
        media_id = self._subir(audio.conteudo, f"resposta.{audio.extensao}", audio.mime_type, "subir áudio")
        return self._enviar(para, {"type": "audio", "audio": {"id": media_id}})

    def enviar_imagem(self, para: str, conteudo: bytes, mime: str, legenda: str = "") -> str:
        media_id = self._subir(conteudo, "grafico.png", mime, "subir imagem")
        imagem = {"id": media_id, **({"caption": legenda[:1024]} if legenda else {})}
        return self._enviar(para, {"type": "image", "image": imagem})

    def enviar_documento(self, para: str, conteudo: bytes, nome: str, mime: str, legenda: str = "") -> str:
        media_id = self._subir(conteudo, nome, mime, "subir documento")
        documento = {"id": media_id, "filename": nome, **({"caption": legenda[:1024]} if legenda else {})}
        return self._enviar(para, {"type": "document", "document": documento})

    def enviar_template(self, para: str, nome: str, idioma: str, parametros: list[str],
                        payload_botao: str | None = None) -> str:
        componentes: list[dict] = []
        if parametros:
            componentes.append({"type": "body", "parameters": [{"type": "text", "text": p} for p in parametros]})
        if payload_botao:
            componentes.append({"type": "button", "sub_type": "quick_reply", "index": "0",
                                "parameters": [{"type": "payload", "payload": payload_botao}]})
        return self._enviar(para, {"type": "template", "template": {
            "name": nome, "language": {"code": idioma}, "components": componentes}})

    def _subir(self, conteudo: bytes, nome: str, mime: str, etapa: str) -> str:
        upload = self._http.post(
            f"{self._base}/{self._phone_number_id}/media",
            data={"messaging_product": "whatsapp", "type": mime},
            files={"file": (nome, conteudo, mime)},
        )
        _checar(upload, etapa)
        return upload.json()["id"]

    def _enviar(self, para: str, conteudo: dict) -> str:
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
        mensagens = resposta.json().get("messages") or [{}]
        return mensagens[0].get("id", "")
