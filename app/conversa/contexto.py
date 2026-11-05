"""Contexto que o LLM recebe junto com a fala: conversa recente, pendências, catálogo e últimos movimentos.

Código puro. Os limites vêm dos parâmetros (configuráveis na F3-01). A última pergunta do bot é o sinal mais
forte para o LLM entender respostas curtas ("foi 10 reais", "sim", "foi a conta de luz").
"""

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta

from app.conversa.modelos import EstadoConversa
from app.domain.models import TipoMovimento
from app.domain.replies import fmt_num
from app.domain.units import chave_produto, sem_acento

FORMATO = "%Y-%m-%d %H:%M:%S"


@dataclass
class MensagemHistorico:
    de: str  # "comerciante" | "bot"
    texto: str
    quando: str  # "YYYY-MM-DD HH:MM:SS" local


@dataclass
class LimitesContexto:
    max_mensagens: int = 6
    janela_horas: int = 2
    max_produtos: int = 40
    max_movimentos: int = 5


@dataclass
class Contexto:
    agora: str
    historico: list[dict] = field(default_factory=list)
    pendencias: list[dict] = field(default_factory=list)
    produtos: list[dict] = field(default_factory=list)
    ultimos_movimentos: list[dict] = field(default_factory=list)

    def para_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, separators=(",", ":"))


def _num(valor) -> str | None:
    return fmt_num(valor).replace(",", ".") if valor is not None else None


def montar(texto: str, estado: EstadoConversa, historico: list[MensagemHistorico], agora: str,
           limites: LimitesContexto = LimitesContexto()) -> Contexto:
    momento = datetime.strptime(agora, FORMATO)
    desde = (momento - timedelta(hours=limites.janela_horas)).strftime(FORMATO)
    recentes = [h for h in historico if h.quando >= desde][-limites.max_mensagens:]

    pendencias = [
        {"id": p.id, "tipo": p.tipo, "resumo": p.resumo, "faltando": p.campos_faltando, "pergunta": p.pergunta}
        for p in estado.pendencias if p.id is not None
    ]

    # produtos citados na fala primeiro, depois os mexidos mais recentemente
    fala = sem_acento(texto.lower())
    citados = [k for k, p in estado.estoque.items() if k and (k in fala or chave_produto(p.produto) in fala)]
    por_recencia = sorted(estado.estoque, key=lambda k: estado.estoque[k].atualizado_em or "", reverse=True)
    ordem = list(dict.fromkeys(citados + por_recencia))[:limites.max_produtos]
    produtos = [{
        "produto": estado.estoque[k].produto, "unidade": estado.estoque[k].unidade,
        "quantidade": _num(estado.estoque[k].quantidade),
        "custo_medio": _num(estado.estoque[k].custo_medio) if estado.estoque[k].custo_medio is not None
        else "sem custo",
    } for k in ordem]

    movimentos = [{
        "id": m.id, "tipo": m.tipo.value, "produto": m.produto, "quantidade": _num(m.quantidade),
        "unidade": m.unidade, "valor_total": _num(m.valor_total), "quando": m.data_hora,
    } for m in estado.movimentos if m.tipo != TipoMovimento.AJUSTE_VALOR][-limites.max_movimentos:]

    return Contexto(
        agora=agora,
        historico=[{"de": h.de, "texto": h.texto} for h in recentes],
        pendencias=pendencias,
        produtos=produtos,
        ultimos_movimentos=movimentos,
    )
