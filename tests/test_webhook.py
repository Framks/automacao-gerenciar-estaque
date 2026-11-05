"""Webhook HTTP: assinatura, allowlist (registra ignorada) e status de entrega no banco."""

import hashlib
import hmac
import json

import pytest
from fastapi.testclient import TestClient

import main
from app.config import get_settings
from app.fila.controle import Controle, MemoriaRedis
from tests.test_pipeline import FakeMensagens


@pytest.fixture
def cliente(monkeypatch):
    monkeypatch.setenv("WHATSAPP_APP_SECRET", "segredo")
    monkeypatch.setenv("TELEFONES_PERMITIDOS", "5585999990001")
    get_settings.cache_clear()
    mensagens, processadas = FakeMensagens(), []
    entregas = []
    mensagens.atualizar_entrega = lambda wa, status, erros=None: entregas.append((wa, status, erros)) or True
    monkeypatch.setattr(main.container, "mensagens", lambda: mensagens)
    monkeypatch.setattr(main.container, "controle", lambda: Controle(MemoriaRedis()))
    monkeypatch.setattr(main.container, "fila", lambda: None)
    monkeypatch.setattr(main.container, "pipeline", lambda: type("P", (), {"processar": lambda s, m: processadas.append(m)})())
    yield TestClient(main.app), mensagens, processadas, entregas
    get_settings.cache_clear()


def enviar(c, payload):
    corpo = json.dumps(payload).encode()
    assinatura = "sha256=" + hmac.new(b"segredo", corpo, hashlib.sha256).hexdigest()
    return c.post("/api/webhook", content=corpo, headers={"X-Hub-Signature-256": assinatura,
                                                          "Content-Type": "application/json"})


def mensagem(telefone, wa_id="wamid.X"):
    return {"entry": [{"changes": [{"value": {"contacts": [{"wa_id": telefone, "profile": {"name": "Z"}}],
                                               "messages": [{"from": telefone, "id": wa_id, "type": "text",
                                                             "text": {"body": "oi"}}]}}]}]}


def test_assinatura_invalida(cliente):
    c, *_ = cliente
    assert c.post("/api/webhook", content=b"{}", headers={"X-Hub-Signature-256": "sha256=errada"}).status_code == 401


def test_permitido_e_processado(cliente):
    c, mensagens, processadas, _ = cliente
    assert enviar(c, mensagem("5585999990001")).status_code == 200
    assert [m.msg_id for m in processadas] == ["wamid.X"] and mensagens.linhas == {}


def test_fora_da_lista_fica_registrado_como_ignorada_sem_conteudo(cliente):
    c, mensagens, processadas, _ = cliente
    enviar(c, mensagem("5511988887777", "wamid.Y"))
    assert processadas == []
    linha = mensagens.de("wamid.Y")
    assert (linha["status"], linha["texto"], linha["telefone_mascarado"], linha["comerciante_id"]) == (
        "ignorada", "", "5511****7777", None)


def test_status_de_entrega_vai_para_o_banco(cliente):
    c, _, _, entregas = cliente
    enviar(c, {"entry": [{"changes": [{"value": {"statuses": [
        {"id": "wamid.S1", "status": "read", "recipient_id": "5585999990001", "timestamp": "1"},
        {"id": "wamid.S2", "status": "failed", "recipient_id": "5585999990001",
         "errors": [{"code": 130497, "title": "restrito"}]},
    ]}}]}]})
    assert entregas[0] == ("wamid.S1", "read", [])
    assert entregas[1][0:2] == ("wamid.S2", "failed") and entregas[1][2][0]["code"] == 130497
