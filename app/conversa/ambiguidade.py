"""Quando confirmar antes de gravar. Regras em código puro, testadas uma a uma.

Confirma uma compra/venda quando:
- a confiança do LLM está abaixo do limite;
- há valor citado sem verbo de compra/venda na fala ("o feijão foi 10 reais"): pode ser venda ou o preço pago;
- o preço por unidade foge muito do custo médio (compra mais de 3× ou menos de 1/3; venda abaixo de 1/3);
- o valor total passa do limite configurado.
"""

import re
from dataclasses import dataclass
from decimal import Decimal

from app.conversa.modelos import EstadoConversa, Parametros
from app.domain.dinheiro import ValoresIncoerentes, resolver_valores
from app.domain.operacoes import Operacao, TipoOperacao
from app.domain.units import chave_produto, sem_acento

_VERBOS = re.compile(
    r"\b(compr\w*|vend\w*|pagu\w*|paga\w*|gast\w*|cheg\w*|receb\w*|rep(u|o)\w*|entr\w*|sai\w*|levou|lev\w*|"
    r"adquir\w*|adicion\w*|acrescent\w*|coloc\w*|bot\w*|peg\w*|custou|custa\w*|sobr\w*)\b"
)


@dataclass
class Duvida:
    motivo: str  # confianca | valor_sem_verbo | preco_fora | valor_alto
    pergunta: str


def tem_verbo_de_operacao(texto: str) -> bool:
    return bool(_VERBOS.search(sem_acento(texto.lower())))


def _unitario(item) -> Decimal | None:
    try:
        unit, _ = resolver_valores(item.quantidade, item.valor_unitario, item.valor_total)
    except ValoresIncoerentes:
        return None
    return unit


def _total(item) -> Decimal | None:
    try:
        _, total = resolver_valores(item.quantidade, item.valor_unitario, item.valor_total)
    except ValoresIncoerentes:
        return None
    return total


def avaliar_duvida(op: Operacao, texto: str, estado: EstadoConversa, p: Parametros, descrever) -> Duvida | None:
    """`descrever(op)` monta a frase da operação ("uma venda de 1 quilo de feijão por 10 reais")."""
    if op.tipo not in (TipoOperacao.COMPRA, TipoOperacao.VENDA) or not p.confirmacao_ambiguidade:
        return None
    resumo = descrever(op)
    sim_ou_nao = "Está certo? Diga sim ou não."

    if op.confianca < p.confianca_minima:
        return Duvida("confianca", f"Só para confirmar: foi {resumo}? {sim_ou_nao}")

    com_valor = [i for i in op.itens if i.valor_unitario is not None or i.valor_total is not None]
    if com_valor and not tem_verbo_de_operacao(texto):
        item = com_valor[0]
        chave = chave_produto(item.produto)
        pendente = next((pd for pd in reversed(estado.pendencias) if pd.tipo == "completar" and pd.movimento_ref
                         and chave_produto(pd.operacao.get("itens", [{}])[pd.item].get("produto", "")) == chave), None)
        if pendente is not None:
            return Duvida("valor_sem_verbo",
                          f"Só para confirmar: foi {resumo}, ou é o preço que você pagou em {pendente.resumo}? "
                          "Diga: foi venda. Ou: foi o preço da compra.")
        return Duvida("valor_sem_verbo", f"Só para confirmar: foi {resumo}? {sim_ou_nao}")

    for item in com_valor:
        produto = estado.estoque.get(chave_produto(item.produto))
        unit = _unitario(item)
        if produto is None or produto.custo_medio is None or not produto.custo_medio or unit is None:
            continue
        custo = produto.custo_medio
        fora = unit < custo / 3 or (op.tipo == TipoOperacao.COMPRA and unit > custo * 3)
        if fora:
            return Duvida("preco_fora", f"O preço ficou bem diferente do custo de {produto.produto}. "
                                        f"Só para confirmar: foi {resumo}? {sim_ou_nao}")

    total = sum((t for t in (_total(i) for i in op.itens) if t is not None), Decimal(0))
    if total > p.valor_limite:
        return Duvida("valor_alto", f"Só para confirmar, porque o valor é alto: foi {resumo}? {sim_ou_nao}")
    return None
