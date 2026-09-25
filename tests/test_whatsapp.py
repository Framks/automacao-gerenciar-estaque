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
