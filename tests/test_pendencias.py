"""Motor da conversa (pendências, confirmação, despesas) com operações já estruturadas: testa o fluxo, não o LLM."""

from decimal import Decimal

import pytest

from app.conversa.atendimento import Atendimento
from app.conversa.modelos import Parametros
from app.domain.models import TipoMovimento
from app.domain.operacoes import RespostaLLM
from tests.fakes import LojaMemoria


class Conversa:
    """Simula mensagens em sequência: cada fala vem com as operações que o LLM devolveria."""

    def __init__(self, parametros=Parametros()):
        self.loja = LojaMemoria()
        self.atendimento = Atendimento(parametros)
        self.n = 0
        self.hora = 10

    def fala(self, texto: str, *operacoes: dict, agora: str | None = None) -> str:
        self.n += 1
        agora = agora or f"2026-10-29 {self.hora:02d}:{self.n:02d}:00"
        saida = self.atendimento.processar(RespostaLLM.model_validate({"operacoes": list(operacoes)}),
                                           self.loja.carregar_estado(agora), texto=texto, msg_id=f"m{self.n}",
                                           agora=agora)
        self.loja.gravar(saida.plano)
        return saida.texto

    def produto(self, chave):
        return self.loja.estoque[chave]


def compra(produto, quantidade=None, unidade="kg", **valores):
    return {"tipo": "compra", "itens": [{"produto": produto, "quantidade": quantidade, "unidade": unidade, **valores}],
            "confianca": 0.95}


def venda(produto, quantidade, **valores):
    return {"tipo": "venda", "itens": [{"produto": produto, "quantidade": quantidade, **valores}], "confianca": 0.95}


def completar(pendencia_id=None, **campos):
    return {"tipo": "completar", "pendencia_id": pendencia_id, "campos": campos, "confianca": 0.9}


def test_dialogo_real_do_feijao():
    c = Conversa()
    r1 = c.fala("Comprei 10 kg de feijão.", compra("feijão", 10))
    assert "Entrou 10 quilos de feijão" in r1 and r1.endswith("Quanto você pagou no feijão? Pode falar o preço do "
                                                               "quilo, da unidade ou o total.")
    assert "desfazer" not in r1  # com pergunta, a resposta fica mais curta
    (pd,) = c.loja.abertas()
    assert pd.campos_faltando == ["valor"] and pd.movimento_ref == "m1-0"

    r2 = c.fala("Vendi 2 de feijão", venda("feijão", 2))
    assert "Saiu 2 quilos de feijão" in r2
    assert "Quanto você pagou" not in r2  # não insiste na mesma pergunta a cada mensagem

    r3 = c.fala("O que é proíbe que saiu? O feijão, o quilo foi 10 reais cada.", completar(pd.id, valor_unitario=10))
    assert r3 == ("Certo! O feijão saiu a 10 reais o quilo, 100 reais no total. "
                  "O custo do feijão agora é 10 reais o quilo.")
    ajuste = c.loja.movimentos[-1]
    assert (ajuste.tipo, ajuste.valor_total, ajuste.referencia_mov, ajuste.quantidade) == (
        TipoMovimento.AJUSTE_VALOR, Decimal("100.00"), "m1-0", 0)
    assert c.produto("feijao").quantidade == 8 and c.produto("feijao").custo_medio == Decimal("10.0000")
    assert c.loja.abertas() == []


def test_completar_sem_id_usa_a_pendencia_mais_recente():
    c = Conversa()
    c.fala("comprei 5 kg de carne", compra("carne", 5))
    assert "100 reais no total" in c.fala("foi 100 reais", completar(valor_total=100))
    assert c.produto("carne").custo_medio == Decimal("20.0000")


def test_custo_medio_ao_completar_com_estoque_anterior():
    c = Conversa()
    c.fala("comprei 10 kg de arroz por 50", compra("arroz", 10, valor_total=50))  # custo 5
    c.fala("comprei mais 10 kg de arroz", compra("arroz", 10))  # sem valor: custo fica 5
    assert c.produto("arroz").custo_medio == Decimal("5.0000")
    c.fala("paguei 7 o quilo", completar(valor_unitario=7))
    # 20 kg: 10 a 5 e 10 a 7 -> 6
    assert c.produto("arroz").custo_medio == Decimal("6.0000")


def test_completar_depois_de_vender_parte():
    """Regra documentada: q = min(quantidade da entrada, saldo); o saldo restante absorve a diferença."""
    c = Conversa()
    c.fala("comprei 10 kg de arroz por 50", compra("arroz", 10, valor_total=50))
    c.fala("comprei mais 10 kg", compra("arroz", 10))
    c.fala("vendi 15", venda("arroz", 15))  # saldo 5 a custo 5
    c.fala("o quilo foi 7", completar(valor_unitario=7))
    assert c.produto("arroz").custo_medio == Decimal("7.0000")  # 5 + 5 × (7 − 5) ÷ 5


def test_compra_sem_quantidade_nao_grava_e_pergunta():
    c = Conversa()
    r = c.fala("comprei feijão", compra("feijão", None))
    assert r == "Quantos quilos de feijão?"
    assert c.loja.movimentos == []
    r2 = c.fala("foram 5 quilos", completar(quantidade=5))
    assert "Entrou 5 quilos de feijão" in r2 and r2.endswith("Quanto você pagou no feijão? Pode falar o preço do "
                                                             "quilo, da unidade ou o total.")
    assert c.produto("feijao").quantidade == 5
    assert len(c.loja.abertas()) == 1  # agora falta o valor (desejável)


def test_duas_pendencias_uma_pergunta_por_vez():
    c = Conversa()
    r = c.fala("comprei 2 kg de arroz e 3 de feijão",
               {"tipo": "compra", "itens": [{"produto": "arroz", "quantidade": 2, "unidade": "kg"},
                                            {"produto": "feijão", "quantidade": 3, "unidade": "kg"}]})
    assert r.endswith("Quanto você pagou no arroz? Pode falar o preço do quilo, da unidade ou o total.")
    assert "feijão?" not in r
    arroz, feijao = c.loja.abertas()
    r2 = c.fala("o arroz foi 10", completar(arroz.id, valor_total=10))
    assert r2.endswith("Mais uma coisa: Quanto você pagou no feijão? Pode falar o preço do quilo, da unidade ou o "
                       "total.")
    c.fala("o feijão 9", completar(feijao.id, valor_total=9))
    assert c.loja.abertas() == []


def test_cancelar_pendencia():
    c = Conversa()
    c.fala("comprei 5 kg de carne", compra("carne", 5))
    assert c.fala("não sei o valor", {"tipo": "cancelar"}) == "Tudo bem, deixei como estava."
    assert c.loja.abertas() == [] and c.produto("carne").custo_medio is None


def test_pendencia_vencida_some():
    c = Conversa(Parametros(pendencia_validade_horas=24))
    c.fala("comprei 5 kg de carne", compra("carne", 5), agora="2026-10-29 10:00:00")
    assert c.loja.carregar_estado("2026-10-30 09:59:00").pendencias
    assert c.loja.carregar_estado("2026-10-30 10:00:01").pendencias == []
    assert c.fala("foi 100", completar(valor_total=100), agora="2026-10-30 11:00:00") == \
        "Não tem nenhuma anotação esperando dado."


def test_sem_perguntar_valor_por_flag():
    c = Conversa(Parametros(perguntar_valor_compra=False))
    r = c.fala("comprei 5 kg de carne", compra("carne", 5))
    assert r.endswith("Se estiver errado, diga: desfazer.") and c.loja.abertas() == []


# --- confirmação ---------------------------------------------------------------------------------------

def test_valor_sem_verbo_com_pendencia_pede_confirmacao():
    c = Conversa()
    c.fala("Comprei 10 kg de feijão.", compra("feijão", 10))
    # o LLM errou e devolveu venda; a regra de ambiguidade segura
    r = c.fala("o feijão, o quilo foi 10 reais", venda("feijão", 1, valor_total=10))
    assert r.startswith("Só para confirmar: foi uma venda de 1 quilo de feijão por 10 reais, ou é o preço que você "
                        "pagou em os 10 quilos de feijão?")
    assert len(c.loja.movimentos) == 1  # nada gravado
    # resposta: era o preço da compra -> completar; a confirmação é descartada
    completar_id = next(p.id for p in c.loja.abertas() if p.tipo == "completar")
    c.fala("foi o preço da compra", completar(completar_id, valor_unitario=10))
    assert c.produto("feijao").custo_medio == Decimal("10.0000") and c.produto("feijao").quantidade == 10
    assert c.loja.abertas() == []


def test_confirmar_grava_o_rascunho():
    c = Conversa()
    c.fala("comprei 10 kg de arroz por 50", compra("arroz", 10, valor_total=50))
    r = c.fala("o arroz foi 30 reais", venda("arroz", 2, valor_total=30))
    assert "Diga sim ou não" in r and len(c.loja.movimentos) == 1
    r2 = c.fala("sim", {"tipo": "confirmar"})
    assert "Saiu 2 quilos de arroz por 30 reais" in r2
    assert c.produto("arroz").quantidade == 8


def test_confianca_baixa_e_valor_alto_confirmam():
    c = Conversa()
    baixa = compra("arroz", 2, valor_total=10)
    baixa["confianca"] = 0.4
    assert "Só para confirmar" in c.fala("comprei 2 de arroz por 10", baixa)
    c.fala("não", {"tipo": "cancelar"})
    assert c.loja.movimentos == []
    assert "valor é alto" in c.fala("comprei 100 kg de arroz por 5000", compra("arroz", 100, valor_total=5000))


def test_preco_muito_fora_do_custo_confirma():
    c = Conversa()
    c.fala("comprei 10 kg de arroz por 50", compra("arroz", 10, valor_total=50))
    assert "bem diferente do custo" in c.fala("comprei 1 kg de arroz por 40", compra("arroz", 1, valor_total=40))


def test_outra_operacao_descarta_a_confirmacao():
    c = Conversa()
    c.fala("comprei 10 kg de arroz por 50", compra("arroz", 10, valor_total=50))
    c.fala("o arroz foi 30", venda("arroz", 2, valor_total=30))
    c.fala("quanto tem de arroz?", {"tipo": "consulta", "itens": [{"produto": "arroz"}]})
    assert c.loja.abertas() == []
    assert c.fala("sim", {"tipo": "confirmar"}) == "Não tem nada esperando confirmação."


# --- correção e desfazer -------------------------------------------------------------------------------

def test_corrigir_valor_da_ultima_compra():
    c = Conversa()
    c.fala("comprei 10 kg de arroz por 50", compra("arroz", 10, valor_total=50))
    r = c.fala("errei, o arroz foi 60", {"tipo": "corrigir", "movimento_ref": "ultimo",
                                         "campos": {"produto": "arroz", "valor_total": 60}})
    assert "60 reais no total" in r and c.produto("arroz").custo_medio == Decimal("6.0000")


def test_corrigir_quantidade():
    c = Conversa()
    c.fala("comprei 10 kg de arroz", compra("arroz", 10))
    c.fala("não sei", {"tipo": "cancelar"})
    r = c.fala("na verdade foram 12", {"tipo": "corrigir", "movimento_ref": "ultimo", "campos": {"quantidade": 12}})
    assert r == "Corrigido! Arroz: tinha 10 quilos, agora tem 12 quilos."


def test_desfazer_compra_com_valor_completado_reverte_o_custo():
    c = Conversa()
    c.fala("comprei 10 kg de arroz por 50", compra("arroz", 10, valor_total=50))
    c.fala("comprei 10 kg de arroz", compra("arroz", 10))
    c.fala("o quilo foi 7", completar(valor_unitario=7))
    c.fala("desfazer", {"tipo": "desfazer"})
    assert c.produto("arroz").quantidade == 10 and c.produto("arroz").custo_medio == Decimal("5.0000")


def test_desfazer_fecha_a_pendencia_da_compra_desfeita():
    c = Conversa()
    c.fala("comprei 5 kg de carne", compra("carne", 5))
    c.fala("desfazer", {"tipo": "desfazer"})
    assert c.loja.abertas() == [] and c.produto("carne").quantidade == 0


# --- despesas ------------------------------------------------------------------------------------------

def test_gastei_150_59_pergunta_com_que():
    c = Conversa()
    r = c.fala("gastei 150,59", {"tipo": "despesa", "valor": "150,59"})
    assert r == "Anotei um gasto de 150 reais e 59 centavos. Os 150 reais e 59 centavos foram gastos com quê?"
    (l,) = c.loja.lancamentos
    assert (l.valor, l.categoria) == (Decimal("150.59"), "outros")
    r2 = c.fala("foi a conta de luz", completar(categoria="energia", descricao="conta de luz"))
    assert r2 == "Certo! Anotei que foi com conta de luz."
    assert (l.categoria, l.descricao) == ("energia", "conta de luz") and c.loja.abertas() == []


def test_despesa_com_categoria_grava_direto():
    c = Conversa()
    assert c.fala("paguei 1.200 de aluguel", {"tipo": "despesa", "valor": "1.200", "categoria": "aluguel"}) == \
        "Anotei um gasto de 1200 reais com aluguel."
    assert c.loja.abertas() == []


def test_despesa_sem_valor_pergunta():
    c = Conversa()
    assert c.fala("paguei o frete", {"tipo": "despesa", "categoria": "transporte_frete"}) == "Quanto foi o gasto?"
    assert c.loja.lancamentos == []
    c.fala("foi 80", completar(valor=80))
    assert c.loja.lancamentos[0].valor == Decimal("80.00")
    assert c.loja.lancamentos[0].categoria == "transporte_frete"


def test_quanto_gastei_no_mes():
    c = Conversa()
    c.fala("paguei 1200 de aluguel", {"tipo": "despesa", "valor": 1200, "categoria": "aluguel"})
    c.fala("paguei 300 de luz", {"tipo": "despesa", "valor": 300, "categoria": "energia"})
    c.fala("frete 50", {"tipo": "despesa", "valor": 50, "categoria": "transporte_frete"})
    c.fala("gastei 20 de sacola", {"tipo": "despesa", "valor": 20, "categoria": "embalagem"})
    r = c.fala("quanto gastei esse mês?", {"tipo": "consulta", "assunto": "gastos", "periodo": "mes"})
    assert r == ("Você gastou 1570 reais este mês. Os maiores: aluguel, 1200 reais, energia, 300 reais e "
                 "transporte e frete, 50 reais.")
    r2 = c.fala("quanto gastei com frete?", {"tipo": "consulta", "assunto": "gastos", "categoria": "frete"})
    assert r2 == "Você gastou 50 reais com transporte e frete este mês."


def test_desfazer_despesa():
    c = Conversa()
    c.fala("paguei 300 de luz", {"tipo": "despesa", "valor": 300, "categoria": "energia"})
    assert c.fala("desfazer", {"tipo": "desfazer"}) == "Pronto, desfiz o gasto de 300 reais."
    assert "não anotou gastos" in c.fala("quanto gastei?", {"tipo": "consulta", "assunto": "gastos"})


def test_retirada():
    c = Conversa()
    assert c.fala("tirei 50 pra mim", {"tipo": "retirada", "valor": 50}) == "Anotei uma retirada de 50 reais do caixa."
    assert "não anotou gastos" in c.fala("quanto gastei?", {"tipo": "consulta", "assunto": "gastos"})


def test_varias_operacoes_independentes():
    c = Conversa()
    c.fala("comprei 10 kg de feijão por 50", compra("feijão", 10, valor_total=50))
    r = c.fala("comprei 2 kg de arroz por 10 e vendi 1 kg de feijão",
               compra("arroz", 2, valor_total=10), venda("feijão", 1))
    assert "Entrou 2 quilos de arroz" in r and "Saiu 1 quilo de feijão" in r
    ids = [m.id for m in c.loja.movimentos]
    assert len(ids) == len(set(ids)) == 3  # ids únicos mesmo com duas operações na mesma mensagem


def test_uma_operacao_com_problema_nao_impede_a_outra():
    c = Conversa()
    r = c.fala("vendi 1 de café e comprei 2 de arroz por 10", venda("café", 1), compra("arroz", 2, valor_total=10))
    assert "Não achei café no estoque" in r and "Entrou 2 quilos de arroz" in r


@pytest.mark.parametrize("op", [{"tipo": "conversa"}, None])
def test_conversa(op):
    c = Conversa()
    texto = c.fala("bom dia", *([op] if op else []))
    assert texto.startswith("Oi!")


def test_operacao_ida_e_volta_no_json_nao_vira_milhar():
    from app.domain.operacoes import Operacao

    op = Operacao.model_validate({"tipo": "compra", "itens": [{"produto": "feijão", "quantidade": Decimal("2.500"),
                                                               "valor_total": Decimal("1234.50")}],
                                  "campos": {"valor_unitario": Decimal("10.000")}})
    volta = Operacao.model_validate(op.model_dump(mode="json"))  # como a pendência guarda no banco
    assert volta.itens[0].quantidade == Decimal("2.5") and volta.itens[0].valor_total == Decimal("1234.5")
    assert volta.campos["valor_unitario"] == 10
