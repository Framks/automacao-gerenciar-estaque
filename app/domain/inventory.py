"""Regras de negócio do estoque. Código puro e determinístico: sem rede, sem IA.

Tudo ou nada: se qualquer item de uma mensagem tiver problema, nada é gravado.
"""

from dataclasses import replace
from decimal import Decimal

from app.domain.dinheiro import ValoresIncoerentes, quantidade, resolver_valores, unitario
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

ZERO = Decimal(0)


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
        q = quantidade(item.quantidade) if item.quantidade is not None else None
        if not q or q <= 0:
            resultado.problemas.append(f"Não entendi a quantidade de {nome}.")
            continue
        try:
            valor_unitario, valor_total = resolver_valores(q, item.valor_unitario, item.valor_total)
        except ValoresIncoerentes:
            resultado.problemas.append(
                f"Não entendi o valor de {nome}: o preço por unidade não bate com o total. Pode repetir?"
            )
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
                atual = ProdutoEstoque(produto=nome, unidade=unidade or "un", quantidade=ZERO)
            antes = atual.quantidade
            depois = antes + q
            if valor_total is not None:
                atual.custo_medio = custo_medio_apos_entrada(atual.custo_medio, antes, q, valor_total)
            tipo = TipoMovimento.ENTRADA
        else:
            if atual is None:
                resultado.problemas.append(f"Não achei {nome} no estoque.")
                continue
            antes = atual.quantidade
            if q > antes:
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
                valor_total=valor_total,
                valor_unitario=valor_unitario,
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
                valor_total=valor_total,
            )
        )

    return _fechar(resultado, trabalho)


def custo_medio_apos_entrada(
    custo_atual: Decimal | None, saldo_antes: Decimal, qtd: Decimal, valor_total: Decimal
) -> Decimal:
    """Média ponderada do custo depois de uma entrada com valor.

    Sem custo anterior (ou sem saldo), o custo passa a ser o da entrada. Saldo anterior sem custo
    conhecido não entra na média: só sabemos o preço do que chegou agora.
    """
    if custo_atual is None or saldo_antes <= 0:
        return unitario(valor_total / qtd)
    return unitario((saldo_antes * custo_atual + valor_total) / (saldo_antes + qtd))


def desfazer(movimentos: list[Movimento], estoque: Estoque, *, texto: str, msg_id: str, agora: str) -> Resultado:
    """Desfaz a última mensagem que gravou entradas/saídas, lançando ajustes inversos (nada é apagado)."""
    resultado = Resultado(acao=Acao.DESFAZER)
    ja_desfeitos = {m.referencia for m in movimentos if m.tipo == TipoMovimento.AJUSTE and m.referencia}
    normais = [m for m in movimentos if m.tipo in (TipoMovimento.ENTRADA, TipoMovimento.SAIDA)]
    vigentes = valores_vigentes(movimentos)
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
            if m.quantidade > antes:
                resultado.problemas.append(
                    f"Não dá para desfazer: só tem {fmt_num(antes)} {unidade_falada(atual.unidade, antes)} "
                    f"de {atual.produto}."
                )
                continue
            delta = -m.quantidade
            depois = antes + delta
            valor = vigentes.get(m.id, (None, m.valor_total))[1]
            if valor is not None and atual.custo_medio is not None and depois > 0:
                atual.custo_medio = max(unitario((antes * atual.custo_medio - valor) / depois), ZERO)
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


def valores_vigentes(movimentos: list[Movimento]) -> dict[str, tuple[Decimal | None, Decimal | None]]:
    """{id do movimento: (valor_unitario, valor_total)} já com os ajuste_valor aplicados (vale o último)."""
    vigentes = {m.id: (m.valor_unitario, m.valor_total)
                for m in movimentos if m.tipo in (TipoMovimento.ENTRADA, TipoMovimento.SAIDA)}
    for m in movimentos:
        if m.tipo == TipoMovimento.AJUSTE_VALOR and m.referencia_mov in vigentes:
            vigentes[m.referencia_mov] = (m.valor_unitario, m.valor_total)
    return vigentes


def ajustar_valor(
    original: Movimento,
    estoque: Estoque,
    *,
    valor_unitario: Decimal | None,
    valor_total: Decimal | None,
    valor_anterior: Decimal | None,
    texto: str,
    msg_id: str,
    agora: str,
    indice: int = 0,
) -> Resultado:
    """Completa (ou corrige) o valor de uma entrada/saída já gravada, sem reescrever o livro-caixa.

    Gera um movimento `ajuste_valor` (quantidade 0) que referencia o original. Na entrada, o custo médio
    é recalculado trocando o valor daquelas unidades:
        custo_novo = custo + q × (unitário_novo − unitário_antigo) ÷ saldo,   q = min(quantidade da entrada, saldo)
    Entrada sem valor era contada pelo custo médio da época (aplicar mantém o custo), então o unitário antigo
    é o próprio custo atual. Sem custo conhecido (ou sem saldo), o custo passa a ser o unitário novo.
    """
    resultado = Resultado(acao=Acao.COMPRA if original.tipo == TipoMovimento.ENTRADA else Acao.VENDA)
    chave = chave_produto(original.produto)
    try:
        unit, total = resolver_valores(original.quantidade, valor_unitario, valor_total)
    except ValoresIncoerentes:
        resultado.problemas.append(
            f"Não entendi o valor de {original.produto}: o preço por unidade não bate com o total. Pode repetir?"
        )
        return resultado
    if unit is None:
        resultado.problemas.append(f"Não entendi o valor de {original.produto}.")
        return resultado

    atual = replace(estoque[chave]) if chave in estoque else None
    trabalho: Estoque = {}
    if original.tipo == TipoMovimento.ENTRADA and atual is not None:
        saldo, custo = atual.quantidade, atual.custo_medio
        if custo is None or saldo <= 0:
            atual.custo_medio = unit
        else:
            antigo = unitario(valor_anterior / original.quantidade) if valor_anterior is not None else custo
            q = min(original.quantidade, saldo)
            atual.custo_medio = max(unitario(custo + q * (unit - antigo) / saldo), ZERO)
        atual.atualizado_em = agora
        trabalho[chave] = atual

    saldo_atual = atual.quantidade if atual is not None else ZERO
    resultado.movimentos.append(Movimento(
        id=f"{msg_id}-{indice}", data_hora=agora, tipo=TipoMovimento.AJUSTE_VALOR, produto=original.produto,
        quantidade=ZERO, unidade=original.unidade, valor_total=total, valor_unitario=unit, texto_transcrito=texto,
        whatsapp_msg_id=msg_id, referencia_mov=original.id,
    ))
    resultado.mudancas.append(Mudanca(
        produto=original.produto, unidade=original.unidade, antes=saldo_atual, depois=saldo_atual,
        tipo=TipoMovimento.AJUSTE_VALOR, quantidade=original.quantidade, valor_total=total,
    ))
    return _fechar(resultado, trabalho)


def corrigir_quantidade(
    original: Movimento, estoque: Estoque, *, quantidade_nova: Decimal, texto: str, msg_id: str, agora: str,
    indice: int = 0,
) -> Resultado:
    """Corrige a quantidade de uma entrada/saída já gravada com um `ajuste` da diferença (o custo não muda)."""
    resultado = Resultado(acao=Acao.COMPRA if original.tipo == TipoMovimento.ENTRADA else Acao.VENDA)
    chave = chave_produto(original.produto)
    quantidade_nova = quantidade(quantidade_nova)
    if quantidade_nova <= 0:
        resultado.problemas.append(f"Não entendi a quantidade de {original.produto}.")
        return resultado
    atual = replace(estoque[chave]) if chave in estoque else None
    if atual is None:
        resultado.problemas.append(f"Não achei {original.produto} no estoque.")
        return resultado
    diferenca = quantidade_nova - original.quantidade
    delta = diferenca if original.tipo == TipoMovimento.ENTRADA else -diferenca
    antes = atual.quantidade
    if antes + delta < 0:
        resultado.problemas.append(
            f"Não dá para corrigir: só tem {fmt_num(antes)} {unidade_falada(atual.unidade, antes)} de {atual.produto}."
        )
        return resultado
    atual.quantidade = antes + delta
    atual.atualizado_em = agora
    resultado.movimentos.append(Movimento(
        id=f"{msg_id}-{indice}", data_hora=agora, tipo=TipoMovimento.AJUSTE, produto=atual.produto, quantidade=delta,
        unidade=atual.unidade, valor_total=None, valor_unitario=None, texto_transcrito=texto, whatsapp_msg_id=msg_id,
        referencia_mov=original.id,
    ))
    resultado.mudancas.append(Mudanca(
        produto=atual.produto, unidade=atual.unidade, antes=antes, depois=atual.quantidade, tipo=TipoMovimento.AJUSTE,
        quantidade=delta,
    ))
    return _fechar(resultado, {chave: atual})
