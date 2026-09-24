"""Regras de negócio do estoque. Código puro e determinístico: sem rede, sem IA.

Tudo ou nada: se qualquer item de uma mensagem tiver problema, nada é gravado.
"""

from dataclasses import replace

from app.domain.models import (
    Acao,
    Intencao,
    Movimento,
    Mudanca,
    ProdutoEstoque,
    Resultado,
    TipoMovimento,
)
from app.domain.replies import fmt_num
from app.domain.units import chave_produto, nome_produto, normalizar_unidade, unidade_falada

Estoque = dict[str, ProdutoEstoque]  # chave_produto -> produto

_EPS = 1e-9


def aplicar(intencao: Intencao, estoque: Estoque, *, texto: str, msg_id: str, agora: str) -> Resultado:
    """Aplica compra, venda ou consulta. Para "desfazer" use `desfazer`."""
    if intencao.acao == Acao.CONSULTA:
        return _consultar(intencao, estoque)
    if intencao.acao not in (Acao.COMPRA, Acao.VENDA) or not intencao.itens:
        return Resultado(acao=Acao.DESCONHECIDO)

    resultado = Resultado(acao=intencao.acao)
    trabalho: Estoque = {}  # cópias dos produtos tocados nesta mensagem

    for i, item in enumerate(intencao.itens):
        chave = chave_produto(item.produto)
        nome = nome_produto(item.produto)
        q = item.quantidade
        if not q or q <= 0:
            resultado.problemas.append(f"Não entendi a quantidade de {nome}.")
            continue

        atual = trabalho.get(chave) or (replace(estoque[chave]) if chave in estoque else None)
        unidade = normalizar_unidade(item.unidade)
        if atual and unidade and unidade != atual.unidade:
            resultado.problemas.append(
                f"{atual.produto.capitalize()} está anotado em {unidade_falada(atual.unidade, 2)}. "
                f"Pode repetir falando em {unidade_falada(atual.unidade, 2)}?"
            )
            continue

        if intencao.acao == Acao.COMPRA:
            if atual is None:
                atual = ProdutoEstoque(produto=nome, unidade=unidade or "un", quantidade=0.0)
            antes = atual.quantidade
            depois = antes + q
            if item.valor_total is not None:
                if atual.custo_medio is None or antes <= 0:
                    atual.custo_medio = item.valor_total / q
                else:
                    atual.custo_medio = (antes * atual.custo_medio + item.valor_total) / depois
            tipo = TipoMovimento.ENTRADA
        else:
            if atual is None:
                resultado.problemas.append(f"Não achei {nome} no estoque.")
                continue
            antes = atual.quantidade
            if q > antes + _EPS:
                resultado.problemas.append(
                    f"Só tem {fmt_num(antes)} {unidade_falada(atual.unidade, antes)} de {atual.produto}, "
                    f"não dá para vender {fmt_num(q)}."
                )
                continue
            depois = antes - q
            tipo = TipoMovimento.SAIDA

        atual.quantidade = depois
        atual.atualizado_em = agora
        trabalho[chave] = atual
        resultado.movimentos.append(
            Movimento(
                id=f"{msg_id}-{i}",
                data_hora=agora,
                tipo=tipo,
                produto=atual.produto,
                quantidade=q,
                unidade=atual.unidade,
                valor_total=item.valor_total,
                valor_unitario=(item.valor_total / q) if item.valor_total is not None else None,
                texto_transcrito=texto,
                whatsapp_msg_id=msg_id,
            )
        )
        resultado.mudancas.append(
            Mudanca(
                produto=atual.produto,
                unidade=atual.unidade,
                antes=antes,
                depois=depois,
                tipo=tipo,
                quantidade=q,
                valor_total=item.valor_total,
            )
        )

    return _fechar(resultado, trabalho)


def desfazer(movimentos: list[Movimento], estoque: Estoque, *, texto: str, msg_id: str, agora: str) -> Resultado:
    """Desfaz a última mensagem que gravou entradas/saídas, lançando ajustes inversos (nada é apagado)."""
    resultado = Resultado(acao=Acao.DESFAZER)
    ja_desfeitos = {m.referencia for m in movimentos if m.tipo == TipoMovimento.AJUSTE and m.referencia}
    normais = [m for m in movimentos if m.tipo in (TipoMovimento.ENTRADA, TipoMovimento.SAIDA)]
    alvo = next((m.whatsapp_msg_id for m in reversed(normais) if m.whatsapp_msg_id not in ja_desfeitos), None)
    if alvo is None:
        resultado.problemas.append("Não tem nada para desfazer.")
        return resultado

    trabalho: Estoque = {}
    for i, m in enumerate(mov for mov in normais if mov.whatsapp_msg_id == alvo):
        chave = chave_produto(m.produto)
        atual = trabalho.get(chave) or (replace(estoque[chave]) if chave in estoque else None)
        if atual is None:
            resultado.problemas.append(f"Não achei {m.produto} no estoque para desfazer.")
            continue
        antes = atual.quantidade
        if m.tipo == TipoMovimento.ENTRADA:
            if m.quantidade > antes + _EPS:
                resultado.problemas.append(
                    f"Não dá para desfazer: só tem {fmt_num(antes)} {unidade_falada(atual.unidade, antes)} "
                    f"de {atual.produto}."
                )
                continue
            delta = -m.quantidade
            depois = antes + delta
            if m.valor_total is not None and atual.custo_medio is not None and depois > _EPS:
                atual.custo_medio = max((antes * atual.custo_medio - m.valor_total) / depois, 0.0)
        else:
            delta = m.quantidade
            depois = antes + delta

        atual.quantidade = depois
        atual.atualizado_em = agora
        trabalho[chave] = atual
        resultado.movimentos.append(
            Movimento(
                id=f"{msg_id}-{i}",
                data_hora=agora,
                tipo=TipoMovimento.AJUSTE,
                produto=atual.produto,
                quantidade=delta,
                unidade=atual.unidade,
                valor_total=None,
                valor_unitario=None,
                texto_transcrito=texto,
                whatsapp_msg_id=msg_id,
                referencia=alvo,
            )
        )
        resultado.mudancas.append(
            Mudanca(
                produto=atual.produto,
                unidade=atual.unidade,
                antes=antes,
                depois=depois,
                tipo=TipoMovimento.AJUSTE,
                quantidade=delta,
            )
        )

    return _fechar(resultado, trabalho)


def _consultar(intencao: Intencao, estoque: Estoque) -> Resultado:
    resultado = Resultado(acao=Acao.CONSULTA)
    if not intencao.itens:
        resultado.consulta = sorted(estoque.values(), key=lambda p: p.produto)
        return resultado
    for item in intencao.itens:
        chave = chave_produto(item.produto)
        if chave in estoque:
            resultado.consulta.append(estoque[chave])
        else:
            resultado.nao_encontrados.append(nome_produto(item.produto))
    return resultado


def _fechar(resultado: Resultado, trabalho: Estoque) -> Resultado:
    if resultado.problemas:  # tudo ou nada
        resultado.movimentos.clear()
        resultado.mudancas.clear()
        return resultado
    resultado.estoque_alterado = list(trabalho.values())
    return resultado
