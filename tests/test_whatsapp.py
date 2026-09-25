import json
import hashlib
import hmac

from app.fila.controle import Controle, MemoriaRedis, TravaOcupada
from app.whatsapp.client import assinatura_valida, extrair_mensagens

PAYLOAD = {
    "object": "whatsapp_business_account",
    "entry": [{
        "id": "WABA",
        "changes": [{
            "field": "messages",
            "value": {
                "messaging_product": "whatsapp",
                "contacts": [{"profile": {"name": "Maria"}, "wa_id": "5585999990001"}],
                "messages": [
                    {"from": "5585999990001", "id": "wamid.A", "type": "audio",
                     "audio": {"id": "MEDIA1", "mime_type": "audio/ogg; codecs=opus", "voice": True}},
                    {"from": "5585999990001", "id": "wamid.B", "type": "text", "text": {"body": "vendi 2 kg"}},
                    {"from": "5585999990001", "id": "wamid.C", "type": "image", "image": {"id": "IMG"}},
                ],
            },
        }],
    }],
}


def test_extrair_mensagens():
    audio, texto = extrair_mensagens(PAYLOAD)
    assert (audio.tipo, audio.media_id, audio.nome, audio.msg_id) == ("audio", "MEDIA1", "Maria", "wamid.A")
    assert (texto.tipo, texto.texto) == ("text", "vendi 2 kg")


def test_status_de_entrega_e_ignorado():
    assert extrair_mensagens({"entry": [{"changes": [{"value": {"statuses": [{"id": "x"}]}}]}]}) == []


def test_assinatura():
    corpo = b'{"a":1}'
    sig = "sha256=" + hmac.new(b"segredo", corpo, hashlib.sha256).hexdigest()
    assert assinatura_valida(corpo, sig, "segredo")
    assert not assinatura_valida(corpo, sig, "outro")
    assert not assinatura_valida(corpo, None, "segredo")


def test_dedupe_e_trava():
    c = Controle(MemoriaRedis())
    assert c.primeira_vez("m1")
    assert not c.primeira_vez("m1")
    c.esquecer("m1")
    assert c.primeira_vez("m1")

    with c.trava("tel"):
        try:
            with c.trava("tel", espera=0.1):
                raise AssertionError("não deveria entrar")
        except TravaOcupada:
            pass
    with c.trava("tel", espera=0.1):  # liberada ao sair
        pass


def test_config_ignora_espacos_colados(monkeypatch):
    from app.config import Settings

    monkeypatch.setenv("PUBLIC_BASE_URL", "  https://bot.vercel.app/ ")
    monkeypatch.setenv("QSTASH_URL", " https://qstash-eu-central-1.upstash.io")
    s = Settings(_env_file=None)
    assert s.worker_url == "https://bot.vercel.app/api/worker"
    assert s.qstash_url == "https://qstash-eu-central-1.upstash.io"


def test_worker_url_usa_dominio_da_vercel_se_faltar_public_base_url(monkeypatch):
    from app.config import Settings

    monkeypatch.delenv("PUBLIC_BASE_URL", raising=False)
    monkeypatch.setenv("VERCEL_PROJECT_PRODUCTION_URL", "bot.vercel.app")
    assert Settings(_env_file=None).worker_url == "https://bot.vercel.app/api/worker"
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://meu-dominio.com")
    assert Settings(_env_file=None).worker_url == "https://meu-dominio.com/api/worker"


def test_numero_para_envio_adiciona_nono_digito_no_brasil():
    from app.whatsapp.client import numero_para_envio

    assert numero_para_envio("558881061891") == "5588981061891"  # celular sem o 9 -> com 9
    assert numero_para_envio("5588981061891") == "5588981061891"  # já tem 13 dígitos
    assert numero_para_envio("558832221111") == "558832221111"  # fixo (começa com 3): não mexe
    assert numero_para_envio("15551647290") == "15551647290"  # outro país: não mexe


def test_erro_da_meta_aparece_na_mensagem():
    import httpx
    import pytest

    from app.whatsapp.client import ErroWhatsApp, _checar

    erro = {"error": {"code": 131030, "message": "Recipient phone number not in allowed list"}}
    resposta = httpx.Response(400, json=erro)
    with pytest.raises(ErroWhatsApp, match="131030: Recipient phone number not in allowed list"):
        _checar(resposta, "enviar text")


def test_extrair_status_de_entrega_com_erro():
    from app.whatsapp.client import extrair_status

    payload = {"entry": [{"changes": [{"value": {"statuses": [{
        "id": "wamid.ABCDEFGHIJKLMNOP", "status": "failed", "timestamp": "1790300000",
        "recipient_id": "5588981061891",
        "errors": [{"code": 131047, "title": "Re-engagement message", "message": "Re-engagement message",
                    "error_data": {"details": "Message failed to send because more than 24 hours have passed"}}],
    }]}}]}]}
    (evento,) = extrair_status(payload)
    assert evento["status"] == "failed" and evento["para"] == "5588****1891"
    assert evento["erros"][0]["code"] == 131047
    assert "24 hours" in evento["erros"][0]["details"]


def test_registrar_status_guarda_so_os_ultimos():
    c = Controle(MemoriaRedis())
    for i in range(5):
        c.registrar_status({"i": i}, limite=3)
    assert [json.loads(x)["i"] for x in c._redis.lrange("wa:status", 0, -1)] == [4, 3, 2]
