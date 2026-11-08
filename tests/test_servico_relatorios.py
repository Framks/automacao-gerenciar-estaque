"""Serviço de relatórios (F6-03/F6-05): formatos de resposta, planilha pt-BR e o gráfico (dados + PNG válido)."""

import csv
import io
from datetime import date

from app.conversa.modelos import Parametros, PedidoRelatorio
from app.domain.periodos import resolver_periodo
from app.servicos import graficos
from app.servicos.relatorios import ServicoRelatorios, csv_ptbr, nome_arquivo
from tests.test_pendencias import Conversa, compra, venda
from tests.test_relatorios import livro

HOJE = date(2026, 11, 4)


def servico(c, grafico=graficos.desenhar):
    return ServicoRelatorios(c.loja, grafico=grafico)


def test_resumo_e_fala_sem_tabela():
    r = servico(livro()).montar(PedidoRelatorio("semana", "resumo"), HOJE)
    assert "```" in r.texto and "R$ 30,00" in r.texto
    assert "```" not in r.fala and r.fala.endswith("Mandei a tabela por escrito.")
    assert r.anexos == []


def test_planilha_ptbr():
    r = servico(livro()).montar(PedidoRelatorio("semana", "planilha"), HOJE)
    (anexo,) = r.anexos
    assert (anexo.tipo, anexo.mime, anexo.nome) == ("document", "text/csv", "caixa_2026-11-02_a_2026-11-04.csv")
    assert anexo.conteudo.startswith("﻿".encode())
    linhas = list(csv.DictReader(io.StringIO(anexo.conteudo.decode("utf-8-sig")), delimiter=";"))
    tipos = [l["tipo"] for l in linhas]
    assert tipos.count("venda") == 3 and "pagamento de fiado" in tipos and "despesa" in tipos
    pix = next(l for l in linhas if l["tipo"] == "venda" and l["meio_pagamento"] == "Pix")
    assert pix["valor"] == "16" and pix["data"] == "02/11/2026"  # corrigido de 14 para 16
    fiado = next(l for l in linhas if l["meio_pagamento"] == "fiado")
    assert fiado["cliente"] == "João"
    assert r.texto.startswith("Mandei a planilha do caixa (esta semana (02/11 a 04/11))")


def test_planilha_vazia_nao_manda_arquivo():
    r = ServicoRelatorios(Conversa().loja).montar(PedidoRelatorio("ontem", "planilha"), HOJE)
    assert r.anexos == [] and r.texto.startswith("Não tem nada anotado")


def test_csv_numero_com_virgula():
    conteudo = csv_ptbr([{"data": "01/11/2026", "hora": "10:00", "tipo": "venda", "produto_ou_descricao": "arroz",
                          "quantidade": __import__("decimal").Decimal("1.500"), "unidade": "kg",
                          "valor": __import__("decimal").Decimal("7.50"), "meio_pagamento": "cartao", "cliente": ""}])
    assert conteudo.decode("utf-8-sig").splitlines()[1] == "01/11/2026;10:00;venda;arroz;1,5;kg;7,5;cartão;"


def test_nome_do_arquivo():
    assert nome_arquivo(resolver_periodo("outubro", HOJE)) == "caixa_2026-10.csv"
    assert nome_arquivo(resolver_periodo("ontem", HOJE)) == "caixa_2026-11-03.csv"


def test_grafico_com_poucos_dias_manda_a_tabela():
    r = servico(livro()).montar(PedidoRelatorio("dia:2", "grafico"), HOJE)
    assert r.anexos == [] and "Poucos dias com movimento" in r.texto


def mes_com_movimento() -> Conversa:
    c = Conversa(Parametros(confirmacao_ambiguidade=False))
    c.fala("comprei", compra("arroz", 1000, valor_total=3000), agora="2026-10-01 07:00:00")
    for dia in range(1, 32, 2):
        c.fala("vendi", venda("arroz", 10, valor_total=100 + dia * 7) | {"forma_pagamento": "pix"},
               agora=f"2026-10-{dia:02d} 10:00:00")
    c.fala("gastei", {"tipo": "despesa", "valor": 250, "categoria": "aluguel"}, agora="2026-10-05 09:00:00")
    return c


def test_grafico_do_mes():
    c = mes_com_movimento()
    r = servico(c).montar(PedidoRelatorio("outubro", "grafico"), date(2026, 11, 8))
    (img,) = r.anexos
    assert (img.tipo, img.mime) == ("image", "image/png")
    assert img.conteudo[:8] == b"\x89PNG\r\n\x1a\n" and len(img.conteudo) < graficos.LIMITE_BYTES
    assert img.legenda.startswith("Caixa (outubro de 2026): vendas R$ 3.392,00, saídas R$ 3.250,00")
    assert r.fala.endswith("Mandei o gráfico.")


def test_dados_do_grafico_batem_com_a_tabela():
    c = mes_com_movimento()
    rel_ = ServicoRelatorios(c.loja).calcular(resolver_periodo("outubro", date(2026, 11, 8)))
    g = graficos.dados_do_grafico(rel_)
    assert g["dias"] == list(range(1, 32))
    assert sum(g["vendas"]) == rel_.vendas and g["saldo_acumulado"][-1] == rel_.saldo
    assert g["subtitulo"] == "Vendas R$ 3.392,00 · Saídas R$ 3.250,00 · Saldo R$ 142,00"
