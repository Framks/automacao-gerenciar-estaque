"""Fiado: o livro da dívida de cada cliente. Só recebe linhas novas; o saldo é a soma. Código puro.

Sinais: dívida (+), pagamento (−), perdão (−), ajuste (±, para corrigir e desfazer).
A venda fiado conta como venda no dia em que foi feita; o dinheiro só entra no caixa no dia do pagamento.
"""

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from decimal import Decimal

from app.domain.dinheiro import reais

ZERO = Decimal(0)
FORMATO = "%Y-%m-%d %H:%M:%S"
TIPOS = ("divida", "pagamento", "ajuste", "perdao")


@dataclass
class Cliente:
    nome: str
    chave: str
    saldo: Decimal = ZERO  # quanto deve hoje (cache do livro)
    divida_desde: str = ""  # quando o saldo saiu do zero pela última vez ("" se não deve)
    id: int | None = None
    atualizado_em: str = ""


@dataclass
class LancamentoFiado:
    id: str  # id de domínio: "<wa_msg_id>-<posição>"
    data_hora: str
    cliente: str  # chave do cliente
    cliente_nome: str
    tipo: str  # divida | pagamento | ajuste | perdao
    valor: Decimal  # com sinal
    movimento_ref: str = ""  # dívida: a venda (movimento) que a gerou
    forma_pagamento: str = ""  # pagamento: como o cliente pagou
    texto: str = ""
    wa_msg_id: str = ""
    referencia: str = ""  # ajuste de desfazer: id do lançamento desfeito


def aplicar(cliente: Cliente, lanc: LancamentoFiado) -> Cliente:
    """O cliente depois do lançamento (cópia)."""
    novo = replace(cliente)
    antes = cliente.saldo
    novo.saldo = reais(antes + lanc.valor)
    if antes <= 0 < novo.saldo:
        novo.divida_desde = lanc.data_hora
    if novo.saldo <= 0:
        novo.divida_desde = ""
    novo.atualizado_em = lanc.data_hora
    return novo


def desfeitos(fiado: list[LancamentoFiado]) -> set[str]:
    return {l.referencia for l in fiado if l.tipo == "ajuste" and l.referencia}


def dividas_da_venda(fiado: list[LancamentoFiado], movimento_ids: set[str]) -> list[LancamentoFiado]:
    """Dívidas (ainda valendo) geradas pelas vendas indicadas."""
    ja = desfeitos(fiado)
    return [l for l in fiado if l.tipo == "divida" and l.movimento_ref in movimento_ids and l.id not in ja]


def valor_da_divida(fiado: list[LancamentoFiado], divida: LancamentoFiado) -> Decimal:
    """Valor atual de uma dívida: o original mais os ajustes de valor que apontam para a mesma venda."""
    total = divida.valor
    for l in fiado:
        if l.tipo == "ajuste" and not l.referencia and l.movimento_ref == divida.movimento_ref \
                and l.cliente == divida.cliente and l.data_hora >= divida.data_hora and l.id != divida.id:
            total += l.valor
    return reais(total)


def ultimas_dividas(fiado: list[LancamentoFiado], chave: str, n: int = 3) -> list[LancamentoFiado]:
    ja = desfeitos(fiado)
    return [l for l in fiado if l.cliente == chave and l.tipo == "divida" and l.id not in ja][-n:]


def devedores(clientes: dict[str, Cliente], *, dias: int | None = None, agora: str | None = None) -> list[Cliente]:
    """Quem deve, do maior saldo para o menor. Com `dias`: só quem deve há mais de N dias."""
    lista = [c for c in clientes.values() if c.saldo > 0]
    if dias is not None and agora:
        limite = (datetime.strptime(agora, FORMATO) - timedelta(days=dias)).strftime(FORMATO)
        lista = [c for c in lista if c.divida_desde and c.divida_desde <= limite]
    return sorted(lista, key=lambda c: (-c.saldo, c.nome))


def total(clientes: list[Cliente]) -> Decimal:
    return reais(sum((c.saldo for c in clientes), ZERO))
