import pytest

from app.conversa.atalhos import atalho


@pytest.mark.parametrize("fala,periodo", [
    ("fechamento", "hoje"), ("Fechamento do caixa", "hoje"), ("fecha o caixa", "hoje"), ("fechar o caixa!", "hoje"),
    ("me manda o fechamento", "hoje"), ("fechamento de hoje", "hoje"), ("fechamento de ontem", "ontem"),
    ("Manda o fechamento do caixa.", "hoje"), ("como foi o caixa hoje?", "hoje"),
])
def test_fechamento_sem_ia(fala, periodo):
    (op,) = atalho(fala).operacoes
    assert (op.tipo.value, op.assunto, op.periodo, op.formato) == ("consulta", "caixa", periodo, "lista")


@pytest.mark.parametrize("fala", [
    "me manda o fechamento todo dia às 8 da noite", "para de mandar o fechamento", "fechamento do mês",
    "vendi 2 kg de arroz", "caixa de leite", "quanto tem de arroz?", "", None, "não quero o fechamento",
])
def test_resto_vai_para_a_ia(fala):
    assert atalho(fala) is None
