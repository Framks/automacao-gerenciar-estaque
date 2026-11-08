"""Fechamento automático (F6-04): quem recebe e quando (relógio falso), um envio só, janela de 24h e template."""

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from app.db.engine import transacao
from app.db.modelos import FechamentoDB
from app.servicos.configuracoes import DEFINICOES, FLAGS
from app.servicos.fechamento import Devido, Fechamento, Preferencia, dentro_da_janela, devidos
from app.servicos.relatorios import ServicoRelatorios
from app.storage.pg_conversa import PgLoja, PgMensagens
from app.whatsapp.client import FORA_DA_JANELA, ErroWhatsApp
from tests.test_pendencias import venda
from tests.test_pg_conversa import ConversaPg, compra

FORTALEZA = ZoneInfo("America/Fortaleza")


def local(texto: str) -> datetime:
    return datetime.strptime(texto, "%Y-%m-%d %H:%M").replace(tzinfo=FORTALEZA)


# --- regra pura -------------------------------------------------------------------------------------------

def test_devidos_no_horario_e_so_uma_vez():
    p = [Preferencia(1, "America/Fortaleza", "20:00", False)]
    assert devidos(local("2026-11-08 19:45"), p, set()) == []
    assert devidos(local("2026-11-08 20:00"), p, set()) == [Devido(1, "diario", date(2026, 11, 8))]
    assert devidos(local("2026-11-08 21:30"), p, set()) == [Devido(1, "diario", date(2026, 11, 8))]
    assert devidos(local("2026-11-08 22:00"), p, set()) == []  # passou da janela de 2h
    assert devidos(local("2026-11-08 20:15"), p, {(1, "diario", date(2026, 11, 8))}) == []


def test_devidos_usa_o_fuso_do_comerciante():
    # 20h em Fortaleza = 23h UTC; 20h em Manaus = 00h UTC do dia seguinte
    p = [Preferencia(1, "America/Fortaleza", "20:00", False), Preferencia(2, "America/Manaus", "20:00", False)]
    agora = datetime(2026, 11, 8, 23, 5, tzinfo=timezone.utc)
    assert devidos(agora, p, set()) == [Devido(1, "diario", date(2026, 11, 8))]
    assert devidos(agora + timedelta(hours=1), p, set()) == [Devido(1, "diario", date(2026, 11, 8)),
                                                             Devido(2, "diario", date(2026, 11, 8))]


def test_mensal_no_dia_1_com_o_mes_anterior():
    p = [Preferencia(1, "America/Fortaleza", "", True)]
    assert devidos(local("2026-12-01 08:00"), p, set()) == [Devido(1, "mensal", date(2026, 11, 1))]
    assert devidos(local("2026-12-02 08:00"), p, set()) == []
    com_diario = [Preferencia(1, "America/Fortaleza", "20:00", True)]
    assert {d.tipo for d in devidos(local("2026-12-01 20:00"), com_diario, set())} == {"diario", "mensal"}


def test_janela_de_24h():
    agora = local("2026-11-08 20:00")
    assert dentro_da_janela(local("2026-11-08 09:00"), agora)
    assert not dentro_da_janela(local("2026-11-07 19:59"), agora)
    assert not dentro_da_janela(None, agora)


# --- envio com o banco ------------------------------------------------------------------------------------

class Cfg:
    def __init__(self, **valores):
        self.valores = valores

    def get(self, chave, comerciante_id=None):
        return self.valores.get(chave, DEFINICOES[chave].padrao)

    def ligada(self, chave, comerciante_id=None):
        return self.valores.get(chave, FLAGS[chave].padrao)


class WhatsAppFalso:
    def __init__(self, erro_texto=None):
        self.enviados = []
        self.erro_texto = erro_texto

    def enviar_texto(self, para, texto):
        if self.erro_texto:
            raise self.erro_texto
        self.enviados.append(("texto", para, texto))
        return f"wamid.f{len(self.enviados)}"

    def enviar_imagem(self, para, conteudo, mime, legenda=""):
        self.enviados.append(("imagem", para, legenda))
        return f"wamid.f{len(self.enviados)}"

    def enviar_template(self, para, nome, idioma, parametros, payload_botao=None):
        self.enviados.append(("template", para, (nome, idioma, parametros, payload_botao)))
        return f"wamid.f{len(self.enviados)}"


def preparar(fabrica_pg, cfg=None, wa=None, relogio="2026-10-29 20:00"):
    c = ConversaPg(fabrica_pg)
    c.fala("comprei 20 kg de arroz por 100", compra("arroz", 20, valor_total=100), agora="2026-10-29 08:00:00")
    c.fala("vendi 2 kg por 14 no pix", venda("arroz", 2, valor_total=14) | {"forma_pagamento": "pix"},
           agora="2026-10-29 09:00:00")
    mensagens = PgMensagens(fabrica_pg)
    f = Fechamento(fabrica_pg, cfg or Cfg(fechamento_diario_horario="20:00"), wa or WhatsAppFalso(), mensagens,
                   lambda cid: ServicoRelatorios(PgLoja(fabrica_pg, cid)), relogio=lambda: local(relogio))
    return c, f, mensagens


def entrada(mensagens, cid, quando: str):
    mid, _ = mensagens.registrar_entrada(cid, f"wamid.in.{quando}", "audio")
    with transacao(mensagens._fabrica) as s:
        from app.db.modelos import MensagemDB

        s.get(MensagemDB, mid).criado_em = local(quando)


def test_dentro_da_janela_manda_o_relatorio_uma_vez(fabrica_pg):
    wa = WhatsAppFalso()
    c, f, mensagens = preparar(fabrica_pg, wa=wa)
    entrada(mensagens, c.cid, "2026-10-29 09:00")
    assert f.agendar() == {"devidos": 1, "resultados": ["enviado"]}
    ((tipo, para, texto),) = wa.enviados
    assert tipo == "texto" and para == "558881061891"
    assert texto.startswith("Fechamento do caixa: hoje (29/10)") and "R$ 14,00" in texto
    assert f.agendar() == {"devidos": 0, "resultados": []}  # QStash de novo: nada sai duas vezes
    assert f.enviar(c.cid, "diario", "2026-10-29") == "ja_feito"
    assert len(wa.enviados) == 1


def test_fora_da_janela_sem_template_avisa_depois(fabrica_pg):
    wa = WhatsAppFalso()
    c, f, mensagens = preparar(fabrica_pg, wa=wa)
    entrada(mensagens, c.cid, "2026-10-27 09:00")
    assert f.agendar()["resultados"] == ["sem_janela"]
    assert wa.enviados == []
    aviso = f.aviso_pendente(c.cid)
    assert aviso.startswith("O fechamento de 29/10 não foi enviado") and "como foi o caixa do dia 29/10" in aviso
    assert f.aviso_pendente(c.cid) == ""  # avisa uma vez só


def test_fora_da_janela_com_template(fabrica_pg):
    wa = WhatsAppFalso()
    cfg = Cfg(fechamento_diario_horario="20:00", fechamento_template="fechamento_caixa")
    c, f, mensagens = preparar(fabrica_pg, cfg=cfg, wa=wa)
    assert f.agendar()["resultados"] == ["enviado"]
    ((tipo, _, (nome, idioma, params, payload)),) = wa.enviados
    assert (tipo, nome, idioma) == ("template", "fechamento_caixa", "pt_BR")
    assert params == ["hoje (29/10)", "R$ 14,00", "R$ 100,00", "-R$ 86,00"]
    assert payload == "caixa|data:2026-10-29|resumo"
    with transacao(fabrica_pg) as s:
        assert s.scalar(select(FechamentoDB.canal)) == "template"


def test_a_meta_recusa_por_janela_e_cai_no_template(fabrica_pg):
    wa = WhatsAppFalso(erro_texto=ErroWhatsApp("fora da janela", FORA_DA_JANELA))
    cfg = Cfg(fechamento_diario_horario="20:00", fechamento_template="fechamento_caixa")
    c, f, mensagens = preparar(fabrica_pg, cfg=cfg, wa=wa)
    entrada(mensagens, c.cid, "2026-10-29 09:00")
    assert f.agendar()["resultados"] == ["enviado"]
    assert wa.enviados[0][0] == "template"


def test_erro_libera_nova_tentativa(fabrica_pg):
    wa = WhatsAppFalso(erro_texto=RuntimeError("rede"))
    c, f, mensagens = preparar(fabrica_pg, wa=wa)
    entrada(mensagens, c.cid, "2026-10-29 09:00")
    with pytest.raises(RuntimeError):
        f.enviar(c.cid, "diario", "2026-10-29")
    wa.erro_texto = None
    assert f.enviar(c.cid, "diario", "2026-10-29") == "enviado"
    with transacao(fabrica_pg) as s:
        linha = s.scalar(select(FechamentoDB))
        assert (linha.status, linha.tentativas) == ("enviado", 2)


def test_dia_vazio_e_chave_geral(fabrica_pg):
    wa = WhatsAppFalso()
    cfg = Cfg(fechamento_diario_horario="20:00", fechamento_dia_vazio=False)
    c, f, mensagens = preparar(fabrica_pg, cfg=cfg, wa=wa, relogio="2026-10-30 20:00")
    entrada(mensagens, c.cid, "2026-10-30 09:00")
    assert f.agendar()["resultados"] == ["vazio"] and wa.enviados == []

    desligado = Fechamento(fabrica_pg, Cfg(fechamento_automatico=False, fechamento_diario_horario="20:00"), wa,
                           mensagens, lambda cid: None, relogio=lambda: local("2026-10-31 20:00"))
    assert desligado.agendar() == {"desligado": True, "devidos": 0}


def test_mensal_com_grafico(fabrica_pg):
    wa = WhatsAppFalso()
    cfg = Cfg(fechamento_mensal=True)
    c, f, mensagens = preparar(fabrica_pg, cfg=cfg, wa=wa, relogio="2026-11-01 08:05")
    for dia in (10, 15, 20):
        c.fala("vendi", venda("arroz", 1, valor_total=7), agora=f"2026-10-{dia} 10:00:00")
    entrada(mensagens, c.cid, "2026-10-31 18:00")
    assert f.agendar()["resultados"] == ["enviado"]
    assert [e[0] for e in wa.enviados] == ["texto", "imagem"]
    assert wa.enviados[0][2].startswith("Fechamento do mês. Caixa: outubro de 2026 (valores em R$)")
