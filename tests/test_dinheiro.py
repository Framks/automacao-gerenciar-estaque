from decimal import Decimal

import pytest

from app.domain.dinheiro import ValoresIncoerentes, parse_valor, reais, resolver_valores
from app.domain.replies import fmt_num, fmt_reais


@pytest.mark.parametrize("entrada, esperado", [
    ("150,59", "150.59"),
    ("150.59", "150.59"),
    ("1.234,50", "1234.50"),
    ("1.200", "1200"),
    ("R$ 10", "10"),
    ("R$10,00", "10.00"),
    ("10 reais", "10"),
    ("10 reais e 50 centavos", "10.50"),
    ("50 centavos", "0.50"),
    ("12 conto", "12"),
    (10, "10"),
    (10.5, "10.5"),
    (0.1, "0.1"),
    (Decimal("3.333"), "3.333"),
    ("  7 ", "7"),
])
def test_parse_valor(entrada, esperado):
    assert parse_valor(entrada) == Decimal(esperado)


@pytest.mark.parametrize("entrada", [None, "", "   "])
def test_parse_valor_vazio(entrada):
    assert parse_valor(entrada) is None


@pytest.mark.parametrize("entrada", ["abc", "dez reais", "1,2,3", True, float("nan"), "e"])
def test_parse_valor_invalido(entrada):
    with pytest.raises(ValueError):
        parse_valor(entrada)


def test_resolver_so_unitario():
    assert resolver_valores(Decimal(10), Decimal(10), None) == (Decimal("10.0000"), Decimal("100.00"))


def test_resolver_so_total():
    assert resolver_valores(Decimal(3), None, Decimal(10)) == (Decimal("3.3333"), Decimal("10.00"))


def test_resolver_os_dois_coerentes():
    assert resolver_valores(Decimal(3), Decimal("3.33"), Decimal(10)) == (Decimal("3.3300"), Decimal("10.00"))


def test_resolver_os_dois_incoerentes():
    with pytest.raises(ValoresIncoerentes):
        resolver_valores(Decimal(2), Decimal(5), Decimal(50))


def test_resolver_nenhum_e_negativo():
    assert resolver_valores(Decimal(2), None, None) == (None, None)
    with pytest.raises(ValoresIncoerentes):
        resolver_valores(Decimal(2), None, Decimal(-1))


def test_soma_de_centavos_exata_na_fala():
    assert fmt_reais(reais(0.1) + reais(0.2)) == "30 centavos"
    assert fmt_reais(Decimal("150.59")) == "150 reais e 59 centavos"
    assert fmt_num(Decimal("2.500")) == "2,5"
    assert fmt_num(Decimal("303.000")) == "303"
