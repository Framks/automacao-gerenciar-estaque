"""Relatórios do caixa para a conversa e para o admin: carrega o livro do período e monta texto, gráfico e planilha.

As contas são todas de app/domain/relatorios.py; aqui só se busca o livro e se escolhe o formato da resposta.
"""

import csv
import io
from collections.abc import Callable
from datetime import date, datetime, time
from decimal import Decimal
from typing import Protocol
from zoneinfo import ZoneInfo

from app.conversa.modelos import Anexo, PedidoRelatorio, RespostaRelatorio
from app.domain import relatorios as rel
from app.domain.pagamento import NOME as NOME_PAGAMENTO
from app.domain.periodos import Periodo, resolver_periodo
from app.servicos.exportacao import seguro

MINIMO_DIAS_GRAFICO = 3


class Livro(Protocol):
    def carregar_livro(self, desde: datetime) -> tuple[list, list, list]: ...


def nome_arquivo(p: Periodo) -> str:
    if p.tipo == "mes":
        return f"caixa_{p.inicio:%Y-%m}.csv"
    if p.inicio == p.fim:
        return f"caixa_{p.inicio:%Y-%m-%d}.csv"
    return f"caixa_{p.inicio:%Y-%m-%d}_a_{p.fim:%Y-%m-%d}.csv"


def _num(valor) -> str:
    if valor is None:
        return ""
    texto = f"{Decimal(valor).normalize():f}"
    return texto.replace(".", ",")


def csv_ptbr(linhas: list[dict]) -> bytes:
    """CSV que o Excel em pt-BR abre certo: separador ";", vírgula decimal e BOM UTF-8."""
    saida = io.StringIO()
    escritor = csv.writer(saida, delimiter=";", lineterminator="\r\n")
    escritor.writerow(rel.COLUNAS_PLANILHA)
    for l in linhas:
        escritor.writerow([l["data"], l["hora"], seguro(l["tipo"]), seguro(l["produto_ou_descricao"]),
                           _num(l["quantidade"]), seguro(l["unidade"]), _num(l["valor"]),
                           NOME_PAGAMENTO.get(l["meio_pagamento"], l["meio_pagamento"]), seguro(l["cliente"])])
    return ("﻿" + saida.getvalue()).encode("utf-8")


class ServicoRelatorios:
    def __init__(self, livro: Livro, timezone: str = "America/Fortaleza",
                 grafico: Callable[[rel.RelatorioCaixa], bytes] | None = None):
        self._livro = livro
        self._tz = ZoneInfo(timezone)
        self._grafico = grafico

    def _carregar(self, p: Periodo):
        return self._livro.carregar_livro(datetime.combine(p.inicio, time.min, self._tz))

    def calcular(self, p: Periodo) -> rel.RelatorioCaixa:
        return rel.calcular(p, *self._carregar(p))

    def montar(self, pedido: PedidoRelatorio, hoje: date) -> RespostaRelatorio:
        formato = pedido.formato or "resumo"
        periodo_falado = pedido.periodo or ("mes" if formato in ("grafico", "planilha", "por_dia") else None)
        p = resolver_periodo(periodo_falado, hoje)

        if formato == "planilha":
            linhas = rel.linhas_planilha(p, *self._carregar(p))
            if not linhas:
                texto = f"Não tem nada anotado {p.quando} para mandar na planilha."
                return RespostaRelatorio(texto, texto)
            nome = nome_arquivo(p)
            texto = (f"Mandei a planilha do caixa ({p.rotulo}), com {len(linhas)} linhas. "
                     "Ela abre no Excel e no Google Planilhas.")
            return RespostaRelatorio(texto, texto, [Anexo("document", csv_ptbr(linhas), "text/csv", nome)])

        r = self.calcular(p)
        if formato == "grafico":
            if self._grafico is None or len(r.dias_com_movimento) < MINIMO_DIAS_GRAFICO:
                texto = rel.texto_por_dia(r)
                if not r.vazio:
                    texto += "\nPoucos dias com movimento para um gráfico, então mandei a tabela."
                return RespostaRelatorio(texto, rel.fala(r))
            from app.servicos.graficos import legenda

            fala = rel.fala(r).replace(" Mandei a tabela por escrito.", " Mandei o gráfico.")
            return RespostaRelatorio(legenda(r), fala, [Anexo("image", self._grafico(r), "image/png",
                                                              f"{nome_arquivo(p)[:-4]}.png", legenda(r))])
        return RespostaRelatorio(rel.texto(r, formato), rel.fala(r))
