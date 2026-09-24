from app.domain import inventory, replies
from app.domain.models import Acao, Intencao, Item, ProdutoEstoque, Resultado


def test_fmt_num():
    assert replies.fmt_num(303.0) == "303"
    assert replies.fmt_num(2.5) == "2,5"
    assert replies.fmt_num(0.125) == "0,125"


def test_fmt_reais():
    assert replies.fmt_reais(300) == "300 reais"
    assert replies.fmt_reais(1) == "1 real"
    assert replies.fmt_reais(12.5) == "12 reais e 50 centavos"
    assert replies.fmt_reais(0.5) == "50 centavos"


def test_resposta_de_compra_do_exemplo_do_task():
    estoque = {"arroz": ProdutoEstoque("arroz", "kg", 3, None, "", linha=2)}
    i = Intencao(acao=Acao.COMPRA, itens=[Item(produto="arroz", quantidade=300, unidade="kg", valor_total=300)])
    r = inventory.aplicar(i, estoque, texto="", msg_id="m", agora="")
    assert replies.montar(r) == (
        "Anotado! Entrou 300 quilos de arroz por 300 reais. "
        "Tinha 3 quilos, agora tem 303 quilos. Se estiver errado, diga: desfazer."
    )


def test_singular():
    estoque = {"leite": ProdutoEstoque("leite", "cx", 2, None, "", linha=2)}
    i = Intencao(acao=Acao.VENDA, itens=[Item(produto="leite", quantidade=1)])
    r = inventory.aplicar(i, estoque, texto="", msg_id="m", agora="")
    assert "Saiu 1 caixa de leite. Tinha 2 caixas, agora tem 1 caixa." in replies.montar(r)


def test_problema():
    r = Resultado(acao=Acao.VENDA, problemas=["Não achei feijão no estoque."])
    assert replies.montar(r) == "Não anotei nada. Não achei feijão no estoque."


def test_consultas():
    assert replies.montar(Resultado(acao=Acao.CONSULTA)) == "Seu estoque ainda está vazio."
    r = Resultado(
        acao=Acao.CONSULTA,
        consulta=[ProdutoEstoque("arroz", "kg", 303, None), ProdutoEstoque("feijão", "kg", 2.5, None)],
        nao_encontrados=["café"],
    )
    assert replies.montar(r) == "Você tem 303 quilos de arroz e 2,5 quilos de feijão. Não tem café anotado."


def test_desconhecido():
    assert replies.montar(Resultado(acao=Acao.DESCONHECIDO)).startswith("Não entendi.")
