"""Caixa do comerciante: despesas e retiradas (o que não é mercadoria de estoque). Código puro."""

import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal

from app.domain.dinheiro import reais

CATEGORIAS = [
    "mercadoria", "aluguel", "energia", "agua", "internet", "transporte_frete", "funcionario",
    "impostos", "embalagem", "manutencao", "outros",
]
NOMES_CATEGORIA = {
    "mercadoria": "mercadoria", "aluguel": "aluguel", "energia": "energia", "agua": "água", "internet": "internet",
    "transporte_frete": "transporte e frete", "funcionario": "funcionário", "impostos": "impostos",
    "embalagem": "embalagem", "manutencao": "manutenção", "outros": "outros",
}
_SINONIMOS = {
    "luz": "energia", "conta de luz": "energia", "eletricidade": "energia", "agua": "agua", "frete": "transporte_frete",
    "transporte": "transporte_frete", "gasolina": "transporte_frete", "combustivel": "transporte_frete",
    "uber": "transporte_frete", "salario": "funcionario", "funcionarios": "funcionario", "imposto": "impostos",
    "taxa": "impostos", "sacola": "embalagem", "sacolas": "embalagem", "conserto": "manutencao",
}

FORMATO = "%Y-%m-%d %H:%M:%S"


@dataclass
class Lancamento:
    id: str  # id de domínio: "<wa_msg_id>-<posição>"
    data_hora: str
    tipo: str  # despesa | retirada | ajuste
    valor: Decimal
    categoria: str = "outros"
    descricao: str = ""
    texto: str = ""
    wa_msg_id: str = ""
    referencia: str = ""  # em ajuste (desfazer): id do lançamento desfeito


@dataclass
class AlteracaoLancamento:
    """Completar a descrição/categoria é dado descritivo: atualiza a linha, guardando o histórico."""

    id: str
    categoria: str
    descricao: str
    motivo: str = ""


@dataclass
class TotalGastos:
    total: Decimal = Decimal(0)
    por_categoria: dict[str, Decimal] = field(default_factory=dict)
    quantidade: int = 0


def normalizar_categoria(texto: str | None, categorias: list[str] = CATEGORIAS) -> str:
    if not texto:
        return "outros"
    chave = "".join(c for c in unicodedata.normalize("NFD", texto.lower().strip())
                    if unicodedata.category(c) != "Mn").replace(" e ", "_").replace(" ", "_")
    if chave in categorias:
        return chave
    return _SINONIMOS.get(chave.replace("_", " "), _SINONIMOS.get(chave, "outros"))


def inicio_periodo(periodo: str | None, agora: datetime) -> datetime:
    """hoje | semana (últimos 7 dias) | mes (desde o dia 1). Padrão: mês."""
    if periodo == "hoje":
        return agora.replace(hour=0, minute=0, second=0, microsecond=0)
    if periodo == "semana":
        return (agora - timedelta(days=6)).replace(hour=0, minute=0, second=0, microsecond=0)
    return agora.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def total_gastos(lancamentos: list[Lancamento], desde: datetime, categoria: str | None = None) -> TotalGastos:
    """Soma despesas (e seus ajustes) desde uma data. Retiradas não são gasto do negócio."""
    desfeitos = {l.referencia for l in lancamentos if l.tipo == "ajuste" and l.referencia}
    t = TotalGastos()
    for l in lancamentos:
        if l.tipo != "despesa" or l.id in desfeitos:
            continue
        if datetime.strptime(l.data_hora, FORMATO) < desde.replace(tzinfo=None):
            continue
        if categoria and l.categoria != categoria:
            continue
        t.total += l.valor
        t.quantidade += 1
        t.por_categoria[l.categoria] = t.por_categoria.get(l.categoria, Decimal(0)) + l.valor
    t.total = reais(t.total)
    return t


def desfazer_lancamento(alvo: Lancamento, *, texto: str, msg_id: str, agora: str) -> Lancamento:
    return Lancamento(id=f"{msg_id}-0", data_hora=agora, tipo="ajuste", valor=-alvo.valor, categoria=alvo.categoria,
                      descricao=f"desfaz: {alvo.descricao}".strip(), texto=texto, wa_msg_id=msg_id,
                      referencia=alvo.id)
