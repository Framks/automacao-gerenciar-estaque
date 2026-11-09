"""Meio de pagamento e fiado na conversa (F6-01, F6-02): operações já estruturadas, o fluxo é do código."""

from decimal import Decimal

import pytest

from app.conversa.modelos import Parametros
from app.domain import clientes as cli
from app.domain import fiado as fd
from app.domain.pagamento import normalizar_pagamento, resolver_forma
from tests.test_pendencias import Conversa, compra, completar, venda


def recebimento(cliente=None, valor=None, **extra):
    return {"tipo": "recebimento", "cliente": cliente, "valor": valor, "confianca": 0.95, **extra}


def com_estoque(parametros=Parametros()) -> Conversa:
    c = Conversa(parametros)
    c.fala("comprei 20 kg de arroz por 100", compra("arroz", 20, valor_total=100))
    c.fala("comprei 10 kg de feijão por 80", compra("feijão", 10, valor_total=80))
    return c


def saldo(c: Conversa, chave: str) -> Decimal:
    return c.loja.clientes[chave].saldo


def soma_livro(c: Conversa, chave: str) -> Decimal:
    return sum((l.valor for l in c.loja.fiado if l.cliente == chave), Decimal(0))


# --- meio de pagamento ------------------------------------------------------------------------------------

@pytest.mark.parametrize("falado,esperado", [
    ("pix", "pix"), ("no Pix", "pix"), ("PÍX", "pix"), ("débito", "cartao"), ("crédito", "cartao"),
    ("maquininha", "cartao"), ("em espécie", "dinheiro"), ("à vista", "dinheiro"), ("na caderneta", "fiado"),
    ("fiado", "fiado"), ("boleto", None), ("", None), (None, None),
])
def test_normalizar_pagamento(falado, esperado):
    assert normalizar_pagamento(falado) == esperado


def test_padrao_nunca_presume_fiado():
    assert resolver_forma(None, "dinheiro") == ("dinheiro", "padrao")
    assert resolver_forma("pix", "dinheiro") == ("pix", "dito")
    assert resolver_forma(None, "fiado") == ("nao_informado", "")
    assert resolver_forma(None, "nao_informado") == ("nao_informado", "")


def test_venda_no_pix_grava_e_fala_o_meio():
    c = com_estoque()
    r = c.fala("vendi 3 kg de arroz por 18 no pix", venda("arroz", 3, valor_total=18) | {"forma_pagamento": "pix"})
    assert "Recebido no Pix." in r and r.endswith("Se estiver errado, diga: desfazer.")
    m = c.loja.movimentos[-1]
    assert (m.forma_pagamento, m.forma_pagamento_origem) == ("pix", "dito")
    assert m.custo_unitario == Decimal("5.0000")  # custo médio na hora da venda


def test_venda_sem_meio_usa_o_padrao_sem_falar():
    c = com_estoque(Parametros(pagamento_padrao="dinheiro"))
    r = c.fala("vendi 1 kg de arroz por 6", venda("arroz", 1, valor_total=6))
    assert "Recebido" not in r
    m = c.loja.movimentos[-1]
    assert (m.forma_pagamento, m.forma_pagamento_origem) == ("dinheiro", "padrao")


def test_venda_sem_meio_e_sem_padrao_fica_nao_informado():
    c = com_estoque()
    c.fala("vendi 1 kg de arroz por 6", venda("arroz", 1, valor_total=6))
    assert c.loja.movimentos[-1].forma_pagamento == "nao_informado"


def test_compra_com_meio():
    c = Conversa()
    c.fala("comprei 10 kg de feijão por 100 no pix", compra("feijão", 10, valor_total=100) | {"forma_pagamento": "pix"})
    assert c.loja.movimentos[-1].forma_pagamento == "pix"


def test_corrigir_o_meio_nao_mexe_no_valor_nem_no_estoque():
    c = com_estoque()
    c.fala("vendi 2 kg de arroz por 12 no pix", venda("arroz", 2, valor_total=12) | {"forma_pagamento": "pix"})
    r = c.fala("foi no cartão, não no pix",
               {"tipo": "corrigir", "movimento_ref": "ultimo", "campos": {"forma_pagamento": "cartao"}})
    assert r == "Certo! Mudei o pagamento para cartão."
    m = c.loja.movimentos[-1]
    assert (m.forma_pagamento, m.valor_total, m.quantidade) == ("cartao", Decimal("12.00"), 2)
    assert c.produto("arroz").quantidade == 18


def test_perguntar_pagamento_quando_ligado():
    c = com_estoque(Parametros(perguntar_pagamento=True))
    r = c.fala("vendi 1 kg de feijão por 10", venda("feijão", 1, valor_total=10))
    assert r.endswith("Foi em dinheiro, Pix, cartão ou fiado?")
    (pd,) = c.loja.abertas()
    assert c.fala("pix", completar(pd.id, forma_pagamento="pix")) == "Certo! Mudei o pagamento para Pix."
    assert c.loja.movimentos[-1].forma_pagamento == "pix"
    assert c.loja.abertas() == []


def test_sem_pergunta_de_pagamento_por_padrao():
    c = com_estoque()
    c.fala("vendi 1 kg de feijão por 10", venda("feijão", 1, valor_total=10))
    assert c.loja.abertas() == []


# --- fiado ------------------------------------------------------------------------------------------------

def test_venda_fiado_sem_cliente_pergunta_e_so_grava_com_o_nome():
    c = com_estoque()
    r = c.fala("vendi 2 kg de arroz por 12 fiado", venda("arroz", 2, valor_total=12) | {"forma_pagamento": "fiado"})
    assert r == "Fiado pra quem?"
    assert c.produto("arroz").quantidade == 20  # nada gravado ainda
    (pd,) = c.loja.abertas()
    r = c.fala("pro João", completar(pd.id, cliente="João"))
    assert "Saiu 2 quilos de arroz por 12 reais" in r
    assert "Fiado para João, que agora deve 12 reais." in r
    assert c.produto("arroz").quantidade == 18
    assert saldo(c, "joao") == Decimal("12.00")
    venda_mov = c.loja.movimentos[-1]
    assert venda_mov.forma_pagamento == "fiado"
    (divida,) = c.loja.fiado
    assert (divida.tipo, divida.valor, divida.movimento_ref) == ("divida", Decimal("12.00"), venda_mov.id)


def test_venda_fiado_sem_valor_pergunta_o_valor():
    c = com_estoque()
    r = c.fala("vendi 2 kg de arroz fiado pra Maria",
               venda("arroz", 2) | {"forma_pagamento": "fiado", "cliente": "Maria"})
    assert r == "Quanto ficou a venda fiado de arroz?"
    r = c.fala("12 reais", completar(valor_total=12))
    assert "Fiado para Maria, que agora deve 12 reais." in r
    assert saldo(c, "maria") == 12


def test_pagamento_parcial_total_e_consultas():
    c = com_estoque()
    c.fala("vendi 5 kg de arroz por 30 fiado pro João",
           venda("arroz", 5, valor_total=30) | {"forma_pagamento": "fiado", "cliente": "João"})
    c.fala("vendi 2 kg de feijão por 20 fiado pra dona Maria",
           venda("feijão", 2, valor_total=20) | {"forma_pagamento": "fiado", "cliente": "dona Maria"})

    r = c.fala("o João pagou 20 no pix", recebimento("João", 20, forma_pagamento="pix"))
    assert r == "Anotei: João pagou 20 reais no Pix. Ainda deve 10 reais."
    assert c.loja.fiado[-1].forma_pagamento == "pix"

    r = c.fala("quem tá me devendo?", {"tipo": "consulta", "assunto": "fiado"})
    assert r == "Estão devendo: Dona Maria, 20 reais; João, 10 reais. Total: 30 reais."

    r = c.fala("a Maria acertou tudo", recebimento("Maria", tudo=True))
    assert r == "Anotei: Dona Maria pagou 20 reais. Agora não deve mais nada."
    assert saldo(c, "maria") == 0

    r = c.fala("quanto o João me deve?", {"tipo": "consulta", "assunto": "fiado", "cliente": "joao"})
    assert r.startswith("João deve 10 reais, desde o dia 29/10.")
    assert "Últimas compras fiado: dia 29/10, 30 reais." in r

    for chave in ("joao", "maria"):
        assert saldo(c, chave) == soma_livro(c, chave)


def test_pagamento_acima_do_saldo_pede_confirmacao():
    c = com_estoque()
    c.fala("vendi fiado", venda("arroz", 1, valor_total=15) | {"forma_pagamento": "fiado", "cliente": "João"})
    r = c.fala("o João pagou 50", recebimento("João", 50))
    assert r == "João deve só 15 reais. Anoto o pagamento de 15 reais, que quita tudo? Diga sim ou não."
    assert saldo(c, "joao") == 15  # nada gravado
    assert c.fala("sim", {"tipo": "confirmar"}) == "Anotei: João pagou 15 reais. Agora não deve mais nada."
    assert saldo(c, "joao") == 0


def test_pagamento_de_quem_nao_esta_no_fiado():
    c = com_estoque()
    assert c.fala("o Zé pagou 10", recebimento("Zé", 10)) == "Não achei Zé no fiado."


def test_pagamento_sem_valor_pergunta():
    c = com_estoque()
    c.fala("vendi fiado", venda("arroz", 1, valor_total=15) | {"forma_pagamento": "fiado", "cliente": "João"})
    assert c.fala("o João pagou", recebimento("João")) == "Quanto João pagou?"
    assert c.fala("10", completar(valor=10)) == "Anotei: João pagou 10 reais. Ainda deve 5 reais."


def test_desfazer_venda_fiado_devolve_estoque_e_divida():
    c = com_estoque()
    c.fala("vendi fiado", venda("arroz", 2, valor_total=12) | {"forma_pagamento": "fiado", "cliente": "João"})
    r = c.fala("desfazer", {"tipo": "desfazer"})
    assert r.startswith("Pronto, desfiz a última anotação.")
    assert "Tirei do fiado de João, que não deve mais nada." in r
    assert c.produto("arroz").quantidade == 20
    assert saldo(c, "joao") == 0 == soma_livro(c, "joao")


def test_desfazer_pagamento_volta_a_divida():
    c = com_estoque()
    c.fala("vendi fiado", venda("arroz", 2, valor_total=12) | {"forma_pagamento": "fiado", "cliente": "João"})
    c.fala("pagou 5", recebimento("João", 5))
    r = c.fala("desfazer", {"tipo": "desfazer"})
    assert r == "Pronto, desfiz o pagamento de 5 reais de João. Agora deve 12 reais."
    assert c.produto("arroz").quantidade == 18  # a venda continua


def test_perdoar_sempre_confirma():
    c = com_estoque()
    c.fala("vendi fiado", venda("arroz", 2, valor_total=12) | {"forma_pagamento": "fiado", "cliente": "Zé"})
    r = c.fala("esquece a dívida do Zé", {"tipo": "perdoar", "cliente": "Zé"})
    assert r == "Esquecer a dívida de 12 reais de Zé? Diga sim ou não."
    assert c.fala("sim", {"tipo": "confirmar"}) == "Pronto, esqueci a dívida de 12 reais de Zé."
    assert saldo(c, "ze") == 0 and c.loja.fiado[-1].tipo == "perdao"


def test_nome_parecido_pergunta_e_aceita_sim_ou_cliente_novo():
    c = com_estoque()
    c.fala("vendi fiado", venda("arroz", 1, valor_total=6) | {"forma_pagamento": "fiado", "cliente": "João Pedro"})
    r = c.fala("vendi fiado pro João", venda("arroz", 1, valor_total=6) | {"forma_pagamento": "fiado", "cliente": "João"})
    assert r == "Você quis dizer João Pedro, que já está no fiado? Diga sim, ou diga: é cliente novo."
    assert c.fala("sim", {"tipo": "confirmar"}).count("Fiado para João Pedro, que agora deve 12 reais.") == 1

    c.fala("vendi fiado pro João", venda("arroz", 1, valor_total=6) | {"forma_pagamento": "fiado", "cliente": "João"})
    r = c.fala("é cliente novo", completar(cliente_novo=True))
    assert "Fiado para João, que agora deve 6 reais." in r
    assert set(c.loja.clientes) == {"joao pedro", "joao"}


def test_transcricao_com_erro_pequeno_acha_o_mesmo_cliente():
    c = com_estoque()
    c.fala("vendi fiado", venda("arroz", 1, valor_total=6) | {"forma_pagamento": "fiado", "cliente": "Raimunda"})
    r = c.fala("vendi fiado", venda("arroz", 1, valor_total=6) | {"forma_pagamento": "fiado", "cliente": "raimunda"})
    assert "Raimunda, que agora deve 12 reais" in r


def test_mudar_para_fiado_e_voltar():
    c = com_estoque()
    c.fala("vendi 2 kg de arroz por 12", venda("arroz", 2, valor_total=12))
    r = c.fala("essa foi fiado pro João",
               {"tipo": "corrigir", "movimento_ref": "ultimo", "campos": {"forma_pagamento": "fiado", "cliente": "João"}})
    assert r == "Certo! Mudei o pagamento para fiado. Fiado para João, que agora deve 12 reais."
    r = c.fala("não foi fiado, foi pix",
               {"tipo": "corrigir", "movimento_ref": "ultimo", "campos": {"forma_pagamento": "pix"}})
    assert r == "Certo! Mudei o pagamento para Pix. Tirei do fiado de João, que não deve mais nada."
    assert saldo(c, "joao") == 0 == soma_livro(c, "joao")


def test_corrigir_valor_de_venda_fiado_ajusta_a_divida():
    c = com_estoque()
    c.fala("vendi fiado", venda("arroz", 2, valor_total=12) | {"forma_pagamento": "fiado", "cliente": "João"})
    r = c.fala("a venda foi 14", {"tipo": "corrigir", "movimento_ref": "ultimo", "campos": {"valor_total": 14}})
    assert "A conta de João no fiado agora é 14 reais." in r
    assert saldo(c, "joao") == 14 == soma_livro(c, "joao")
    c.fala("desfazer", {"tipo": "desfazer"})
    assert saldo(c, "joao") == 0 == soma_livro(c, "joao")


def test_fiado_desligado():
    c = com_estoque(Parametros(fiado=False))
    r = c.fala("vendi fiado", venda("arroz", 1, valor_total=6) | {"forma_pagamento": "fiado", "cliente": "João"})
    assert "fiado está desligado" in r
    assert c.produto("arroz").quantidade == 20


def test_quem_deve_ha_mais_de_30_dias():
    c = com_estoque()
    c.fala("vendi fiado", venda("arroz", 1, valor_total=6) | {"forma_pagamento": "fiado", "cliente": "Ana"},
           agora="2026-09-01 10:00:00")
    c.fala("vendi fiado", venda("arroz", 1, valor_total=7) | {"forma_pagamento": "fiado", "cliente": "Bia"},
           agora="2026-10-25 10:00:00")
    r = c.fala("quem deve há mais de 30 dias?", {"tipo": "consulta", "assunto": "fiado", "dias": 30},
               agora="2026-10-29 10:00:00")
    assert r == "Estão devendo há mais de 30 dias: Ana, 6 reais. Total: 6 reais."


# --- regras puras -----------------------------------------------------------------------------------------

@pytest.mark.parametrize("falado,chave,nome", [
    ("pro João", "joao", "João"), ("Dona Maria", "maria", "Dona Maria"), ("o seu Zé", "ze", "Seu Zé"),
    ("pra dona maria", "maria", "Dona Maria"), ("JOÃO PEDRO", "joao pedro", "JOÃO PEDRO"),
])
def test_chave_e_nome_do_cliente(falado, chave, nome):
    assert cli.chave_cliente(falado) == chave
    assert cli.nome_cliente(falado) == nome


def test_saldo_e_divida_desde():
    c = fd.Cliente("João", "joao")
    c = fd.aplicar(c, fd.LancamentoFiado("a", "2026-10-01 10:00:00", "joao", "João", "divida", Decimal(10)))
    assert (c.saldo, c.divida_desde) == (10, "2026-10-01 10:00:00")
    c = fd.aplicar(c, fd.LancamentoFiado("b", "2026-10-02 10:00:00", "joao", "João", "divida", Decimal(5)))
    assert c.divida_desde == "2026-10-01 10:00:00"
    c = fd.aplicar(c, fd.LancamentoFiado("c", "2026-10-03 10:00:00", "joao", "João", "pagamento", Decimal(-15)))
    assert (c.saldo, c.divida_desde) == (0, "")
