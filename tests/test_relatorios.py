"""Relatório de caixa (F6-03), períodos e horários falados, tabelas e a configuração do fechamento pela conversa."""

from datetime import date
from decimal import Decimal

import pytest

from app.conversa.modelos import Parametros
from app.domain import relatorios as rel
from app.domain.periodos import parse_horario, resolver_periodo
from app.domain.tabelas import LARGURA_MAXIMA, TabelaLarga, fmt_inteiro, fmt_rs, tabela
from tests.test_pendencias import Conversa, compra, completar, venda

HOJE = date(2026, 11, 8)  # domingo


# --- períodos ---------------------------------------------------------------------------------------------

@pytest.mark.parametrize("falado,inicio,fim,rotulo", [
    (None, date(2026, 11, 8), date(2026, 11, 8), "hoje (08/11)"),
    ("ontem", date(2026, 11, 7), date(2026, 11, 7), "ontem (07/11)"),
    ("semana", date(2026, 11, 2), date(2026, 11, 8), "esta semana (02/11 a 08/11)"),
    ("mes", date(2026, 11, 1), date(2026, 11, 8), "novembro (até 08/11)"),
    ("mes_passado", date(2026, 10, 1), date(2026, 10, 31), "outubro de 2026"),
    ("outubro", date(2026, 10, 1), date(2026, 10, 31), "outubro de 2026"),
    ("mes:12", date(2025, 12, 1), date(2025, 12, 31), "dezembro de 2025"),
    ("dia:3", date(2026, 11, 3), date(2026, 11, 3), "03/11"),
    ("dia 20", date(2026, 10, 20), date(2026, 10, 20), "20/10"),  # dia 20 ainda não chegou: o do mês passado
    ("data:2026-11-05", date(2026, 11, 5), date(2026, 11, 5), "05/11"),
    ("05/11", date(2026, 11, 5), date(2026, 11, 5), "05/11"),
    ("2026-09", date(2026, 9, 1), date(2026, 9, 30), "setembro de 2026"),
    ("março", date(2026, 3, 1), date(2026, 3, 31), "março de 2026"),
])
def test_resolver_periodo(falado, inicio, fim, rotulo):
    p = resolver_periodo(falado, HOJE)
    assert (p.inicio, p.fim, p.rotulo) == (inicio, fim, rotulo)


@pytest.mark.parametrize("falado,hhmm", [
    ("20h", "20:00"), ("20:30", "20:30"), ("19h30", "19:30"), ("8 da noite", "20:00"),
    ("oito e meia da noite", "20:30"), ("às 7 da manhã", "07:00"), ("meio-dia", "12:00"), ("meia noite", "00:00"),
    ("20:07", "20:00"), ("20:08", "20:15"), ("23:55", "23:45"), ("uma da tarde", "13:00"), ("18 horas", "18:00"),
])
def test_parse_horario(falado, hhmm):
    assert parse_horario(falado).hhmm == hhmm


def test_horario_ambiguo_e_desligar():
    assert parse_horario("às 8").ambiguo == 8
    assert parse_horario("8h").ambiguo == 8
    assert parse_horario("desligar").desligar
    assert parse_horario("para de mandar").desligar
    assert parse_horario("amanhã cedo") is None


# --- tabelas ----------------------------------------------------------------------------------------------

def test_formatos_de_dinheiro():
    assert fmt_rs(Decimal("1234.5")) == "R$ 1.234,50"
    assert fmt_rs(Decimal("-3")) == "-R$ 3,00"
    assert fmt_inteiro(Decimal("9870.5")) == "9.870"  # meio para o par
    assert fmt_inteiro(Decimal("12345.6")) == "12.346"


def test_tabela_alinha_e_recusa_linha_larga():
    t = tabela(["a", "valor"], [["arroz", "1,00"], ["feijão", "10,00"]], "lr", rodape=[["total", "11,00"]])
    assert t.splitlines() == ["a      valor", "arroz   1,00", "feijão 10,00", "------------", "total  11,00"]
    with pytest.raises(TabelaLarga):
        tabela(None, [["x" * 40]], "l")


# --- cálculo ----------------------------------------------------------------------------------------------

def livro() -> Conversa:
    """Um livro com de tudo: compras com e sem valor, vendas por meio, fiado, pagamento, despesa, desfeito, ajuste."""
    c = Conversa()
    c.fala("comprei 10 kg de arroz por 50", compra("arroz", 10, valor_total=50), agora="2026-11-02 08:00:00")
    c.fala("comprei 5 kg de feijão", compra("feijão", 5), agora="2026-11-02 08:10:00")  # sem valor (fica pendente)
    c.fala("vendi 2 kg de arroz por 14 no pix", venda("arroz", 2, valor_total=14) | {"forma_pagamento": "pix"},
           agora="2026-11-02 09:00:00")
    c.fala("vendi 1 kg de arroz por 7 em dinheiro", venda("arroz", 1, valor_total=7) | {"forma_pagamento": "dinheiro"},
           agora="2026-11-02 23:50:00")  # 23h50 em Fortaleza: ainda é dia 02
    c.fala("vendi 1 kg de arroz fiado pro João por 7",
           venda("arroz", 1, valor_total=7) | {"forma_pagamento": "fiado", "cliente": "João"},
           agora="2026-11-03 10:00:00")
    c.fala("gastei 30 de luz", {"tipo": "despesa", "valor": 30, "categoria": "energia", "confianca": 0.9},
           agora="2026-11-03 11:00:00")
    c.fala("vendi 1 kg de arroz por 9", venda("arroz", 1, valor_total=9), agora="2026-11-03 12:00:00")
    c.fala("desfazer", {"tipo": "desfazer"}, agora="2026-11-03 12:01:00")  # a venda de 9 sai do relatório
    c.fala("o João pagou 5 no pix", {"tipo": "recebimento", "cliente": "João", "valor": 5, "forma_pagamento": "pix"},
           agora="2026-11-04 10:00:00")
    c.fala("a venda do pix foi 16", {"tipo": "corrigir", "movimento_ref": "m3-0", "campos": {"valor_total": 16}},
           agora="2026-11-04 11:00:00")  # ajuste_valor depois: vale o valor novo
    return c


def calcular(c: Conversa, periodo: str, hoje=date(2026, 11, 4)) -> rel.RelatorioCaixa:
    return rel.calcular(resolver_periodo(periodo, hoje), c.loja.movimentos, c.loja.lancamentos, c.loja.fiado)


def test_relatorio_da_semana():
    r = calcular(livro(), "semana")
    assert r.vendas == Decimal("30.00")  # 16 (pix, corrigido) + 7 (dinheiro) + 7 (fiado); a de 9 foi desfeita
    assert r.por_forma == {"pix": Decimal("16.00"), "dinheiro": Decimal("7.00"), "fiado": Decimal("7.00")}
    assert r.compras == Decimal("50.00") and r.compras_sem_valor == 1
    assert r.despesas == Decimal("30.00") and r.despesas_por_categoria == {"energia": Decimal("30.00")}
    assert r.recebido_fiado == Decimal("5.00")
    assert r.entrou == Decimal("28.00")  # 30 − 7 do fiado + 5 recebido
    assert r.saiu == Decimal("80.00")
    assert r.saldo == Decimal("-52.00")
    assert r.lucro_bruto == Decimal("10.00")  # 30 vendidos − 4 kg × R$ 5,00 de custo


def test_relatorio_por_dia_e_dia_certo_no_fuso():
    r = calcular(livro(), "semana")
    dias = {d.dia.day: d for d in r.dias_com_movimento}
    assert set(dias) == {2, 3, 4}
    assert dias[2].vendas == Decimal("23.00")  # a venda das 23h50 fica no dia 02
    assert (dias[3].vendas, dias[3].vendido_fiado, dias[3].despesas) == (7, 7, 30)
    assert dias[4].recebido_fiado == 5 and dias[4].entrou == 5


def test_relatorio_de_um_dia_e_itens():
    r = calcular(livro(), "dia:2")
    assert r.vendas == Decimal("23.00")
    (arroz,) = [i for i in r.itens if i.tipo == "venda"]
    assert (arroz.quantidade, arroz.valor, arroz.formas) == (3, Decimal("23.00"), ["pix", "dinheiro"])


def test_textos_cabem_no_celular():
    c = livro()
    r = calcular(c, "semana")
    for formato in ("resumo", "itens", "por_dia"):
        texto = rel.texto(r, formato)
        dentro = texto.split("```")[1] if "```" in texto else ""
        assert all(len(l) <= LARGURA_MAXIMA for l in dentro.splitlines()), formato
    resumo = rel.texto(r, "resumo")
    assert "Vendas" in resumo and "R$ 30,00" in resumo and "⚠ 1 compra sem valor" in resumo
    assert rel.fala(r).startswith("Nesta semana, você vendeu 30 reais, sendo 7 reais fiado")
    assert "```" not in rel.fala(r)


def test_mes_inteiro_cabe_numa_mensagem():
    c = Conversa(Parametros(confirmacao_ambiguidade=False))
    c.fala("comprei", compra("arroz", 1000, valor_total=5000), agora="2026-10-01 07:00:00")
    for dia in range(1, 32):
        c.fala("vendi", venda("arroz", 10, valor_total=12345) | {"forma_pagamento": "pix"},
               agora=f"2026-10-{dia:02d} 10:00:00")
    texto = rel.texto(rel.calcular(resolver_periodo("outubro", HOJE), c.loja.movimentos, [], []), "por_dia")
    assert len(texto) < 4096
    assert "382.695" in texto  # total do mês sem centavos


def test_periodo_sem_movimento():
    r = rel.calcular(resolver_periodo("ontem", HOJE), [], [], [])
    assert r.vazio
    assert rel.texto(r) == "Caixa: ontem (07/11). Não teve movimento anotado."
    assert rel.fala(r) == "Não teve movimento anotado ontem."


# --- pedidos e configuração pela conversa --------------------------------------------------------------------

def test_pedido_de_relatorio_vai_para_o_pipeline():
    c = Conversa()
    saida = c.atendimento.processar(
        __import__("app.domain.operacoes", fromlist=["RespostaLLM"]).RespostaLLM.model_validate(
            {"operacoes": [{"tipo": "consulta", "assunto": "caixa", "periodo": "outubro"}]}),
        c.loja.carregar_estado("2026-11-08 10:00:00"), texto="vendas de outubro", msg_id="x", agora="2026-11-08 10:00:00")
    assert saida.texto == ""
    assert [(p.periodo, p.formato) for p in saida.pedidos] == [("outubro", "por_dia")]


def test_configurar_fechamento():
    c = Conversa()
    r = c.fala("me manda o fechamento todo dia às 8 da noite",
               {"tipo": "configurar", "campos": {"fechamento_horario": "8 da noite"}})
    assert r == "Combinado: todo dia às 20h eu te mando o fechamento do caixa. Pode chegar até 15 minutos depois."
    assert c.loja.configuracoes == {"fechamento_diario_horario": "20:00"}

    assert c.fala("às 8", {"tipo": "configurar", "campos": {"fechamento_horario": "às 8"}}) == \
        "8 da manhã ou da noite?"
    assert "às 20h" in c.fala("da noite", completar(fechamento_horario="da noite"))

    assert c.fala("para de mandar", {"tipo": "configurar", "campos": {"fechamento_horario": "desligar"}}) == \
        "Pronto, não vou mais mandar o fechamento do dia."
    assert c.loja.configuracoes["fechamento_diario_horario"] == ""

    c.fala("manda também o do mês", {"tipo": "configurar", "campos": {"fechamento_mensal": True}})
    assert c.loja.configuracoes["fechamento_mensal"] is True


def test_configurar_so_aceita_as_chaves_do_comerciante():
    c = Conversa()
    r = c.fala("muda o modelo da ia", {"tipo": "configurar", "campos": {"groq_model": "outro"}})
    assert r == "Isso eu não consigo mudar por aqui. Fale com o suporte."
    assert c.loja.configuracoes == {}


def test_consultar_configuracao():
    c = Conversa(Parametros(fechamento_horario="20:00", pagamento_padrao="dinheiro"))
    r = c.fala("que horas é o fechamento?", {"tipo": "configurar", "campos": {}})
    assert r.startswith("O fechamento do dia chega às 20h, o do mês está desligado. Ele vem com a lista do que entrou e saiu.")
    assert "eu anoto dinheiro" in r


def test_fechamento_em_lista():
    r = calcular(livro(), "dia:2")
    assert rel.texto_lista(r) == (
        "Fechamento do caixa: 02/11\n\n"
        "Vendido:\n• arroz — 3 kg — R$ 23,00 (Pix, dinheiro)\nTotal vendido: R$ 23,00\n\n"
        "Comprado:\n• arroz — 10 kg — R$ 50,00\n• feijão — 5 kg — sem valor\nTotal comprado: R$ 50,00\n\n"
        "Saldo do caixa: -R$ 27,00\n\n"
        "⚠ 1 compra sem valor")
