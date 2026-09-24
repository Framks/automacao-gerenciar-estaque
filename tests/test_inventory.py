import pytest

from app.domain import inventory
from app.domain.models import Acao, Intencao, Item, Movimento, ProdutoEstoque, TipoMovimento

AGORA = "2026-09-23 10:00:00"


def intencao(acao, *itens):
    return Intencao(acao=acao, itens=[Item(**i) for i in itens])


def aplicar(i, estoque, msg_id="m1"):
    return inventory.aplicar(i, estoque, texto="fala", msg_id=msg_id, agora=AGORA)


def estoque_arroz(qtd=3.0, custo=2.0):
    return {"arroz": ProdutoEstoque("arroz", "kg", qtd, custo, "", linha=2)}


def test_compra_de_produto_novo_cria_linha_e_custo_medio():
    r = aplicar(intencao(Acao.COMPRA, dict(produto="Arroz", quantidade=300, unidade="kg", valor_total=300)), {})
    assert r.ok
    (p,) = r.estoque_alterado
    assert (p.produto, p.unidade, p.quantidade, p.custo_medio, p.linha) == ("arroz", "kg", 300, 1.0, None)
    (m,) = r.movimentos
    assert (m.tipo, m.quantidade, m.valor_unitario, m.whatsapp_msg_id) == (TipoMovimento.ENTRADA, 300, 1.0, "m1")
    assert (r.mudancas[0].antes, r.mudancas[0].depois) == (0, 300)


def test_compra_existente_recalcula_custo_medio_ponderado():
    r = aplicar(intencao(Acao.COMPRA, dict(produto="arroz", quantidade=300, unidade="quilos", valor_total=300)),
                estoque_arroz())
    (p,) = r.estoque_alterado
    assert p.quantidade == 303
    assert p.custo_medio == pytest.approx((3 * 2 + 300) / 303)
    assert p.linha == 2


def test_compra_sem_valor_mantem_custo():
    r = aplicar(intencao(Acao.COMPRA, dict(produto="arroz", quantidade=7)), estoque_arroz())
    (p,) = r.estoque_alterado
    assert (p.quantidade, p.custo_medio, p.unidade) == (10, 2.0, "kg")


def test_nao_altera_o_estoque_original():
    estoque = estoque_arroz()
    aplicar(intencao(Acao.VENDA, dict(produto="arroz", quantidade=1)), estoque)
    assert estoque["arroz"].quantidade == 3


def test_venda_normal():
    r = aplicar(intencao(Acao.VENDA, dict(produto="arroz", quantidade=2, valor_total=10)), estoque_arroz())
    assert r.ok
    assert r.estoque_alterado[0].quantidade == 1
    assert r.estoque_alterado[0].custo_medio == 2.0
    assert r.movimentos[0].tipo == TipoMovimento.SAIDA


def test_venda_maior_que_estoque_nao_grava():
    r = aplicar(intencao(Acao.VENDA, dict(produto="arroz", quantidade=1000)), estoque_arroz())
    assert not r.ok
    assert not r.movimentos and not r.estoque_alterado
    assert "Só tem 3 quilos de arroz" in r.problemas[0]


def test_venda_de_produto_inexistente():
    r = aplicar(intencao(Acao.VENDA, dict(produto="feijão", quantidade=1)), estoque_arroz())
    assert r.problemas == ["Não achei feijão no estoque."]


def test_unidade_diferente_nao_grava():
    r = aplicar(intencao(Acao.COMPRA, dict(produto="arroz", quantidade=2, unidade="sacos")), estoque_arroz())
    assert not r.ok
    assert "quilos" in r.problemas[0]


def test_tudo_ou_nada_com_varios_itens():
    r = aplicar(
        intencao(Acao.VENDA, dict(produto="arroz", quantidade=1), dict(produto="feijão", quantidade=1)),
        estoque_arroz(),
    )
    assert not r.ok and not r.movimentos and not r.estoque_alterado


def test_mesmo_produto_duas_vezes_na_mesma_mensagem():
    r = aplicar(
        intencao(Acao.VENDA, dict(produto="arroz", quantidade=2), dict(produto="arroz", quantidade=2)),
        estoque_arroz(),
    )
    assert not r.ok  # 2 + 2 > 3


def test_varios_itens_ok():
    r = aplicar(
        intencao(Acao.COMPRA, dict(produto="arroz", quantidade=1), dict(produto="feijão", quantidade=5, unidade="kg")),
        estoque_arroz(),
    )
    assert r.ok
    assert {p.produto: p.quantidade for p in r.estoque_alterado} == {"arroz": 4, "feijão": 5}
    assert [m.id for m in r.movimentos] == ["m1-0", "m1-1"]


def test_quantidade_ausente():
    r = aplicar(intencao(Acao.COMPRA, dict(produto="arroz")), estoque_arroz())
    assert r.problemas == ["Não entendi a quantidade de arroz."]


def test_desconhecido_e_compra_sem_itens():
    assert aplicar(intencao(Acao.DESCONHECIDO), {}).acao == Acao.DESCONHECIDO
    assert aplicar(intencao(Acao.COMPRA), {}).acao == Acao.DESCONHECIDO


def test_consulta():
    estoque = estoque_arroz()
    r = aplicar(intencao(Acao.CONSULTA, dict(produto="Arroz"), dict(produto="feijão")), estoque)
    assert [p.produto for p in r.consulta] == ["arroz"]
    assert r.nao_encontrados == ["feijão"]
    assert not r.movimentos
    assert [p.produto for p in aplicar(intencao(Acao.CONSULTA), estoque).consulta] == ["arroz"]


def mov(msg_id, tipo, qtd, valor=None, ref=""):
    return Movimento(f"{msg_id}-0", AGORA, tipo, "arroz", qtd, "kg", valor, None, "", msg_id, ref)


def desfazer(movimentos, estoque, msg_id="d1"):
    return inventory.desfazer(movimentos, estoque, texto="desfazer", msg_id=msg_id, agora=AGORA)


def test_desfazer_ultima_compra():
    # tinha 3 kg a 2,00; comprou 300 kg por 300 -> 303 kg
    estoque = estoque_arroz(303, (6 + 300) / 303)
    r = desfazer([mov("a", TipoMovimento.ENTRADA, 3, 6), mov("b", TipoMovimento.ENTRADA, 300, 300)], estoque)
    assert r.ok
    (m,) = r.movimentos
    assert (m.tipo, m.quantidade, m.referencia) == (TipoMovimento.AJUSTE, -300, "b")
    assert r.estoque_alterado[0].quantidade == 3
    assert r.estoque_alterado[0].custo_medio == pytest.approx(2.0)


def test_desfazer_venda_devolve_ao_estoque():
    r = desfazer([mov("a", TipoMovimento.SAIDA, 2)], estoque_arroz(1))
    assert r.estoque_alterado[0].quantidade == 3
    assert r.movimentos[0].quantidade == 2


def test_desfazer_pula_o_que_ja_foi_desfeito():
    movimentos = [
        mov("a", TipoMovimento.ENTRADA, 3),
        mov("b", TipoMovimento.ENTRADA, 10),
        mov("d1", TipoMovimento.AJUSTE, -10, ref="b"),
    ]
    r = desfazer(movimentos, estoque_arroz(3), msg_id="d2")
    assert r.movimentos[0].referencia == "a"
    assert r.estoque_alterado[0].quantidade == 0


def test_desfazer_sem_nada():
    assert desfazer([], {}).problemas == ["Não tem nada para desfazer."]


def test_desfazer_compra_ja_vendida_nao_deixa_negativo():
    r = desfazer([mov("a", TipoMovimento.ENTRADA, 10)], estoque_arroz(4))
    assert not r.ok and not r.movimentos
