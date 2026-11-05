"""Dinheiro e quantidades em Decimal. Toda conta de valor é feita aqui, nunca pelo LLM.

- valores em reais: 2 casas (ROUND_HALF_EVEN);
- valor unitário e custo médio: 4 casas;
- quantidades: 3 casas.
"""

import re
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation

CENTAVO = Decimal("0.01")
_QUATRO_CASAS = Decimal("0.0001")
_TRES_CASAS = Decimal("0.001")
TOLERANCIA = CENTAVO  # diferença aceita entre "unitário × quantidade" e o total falado

Numero = Decimal | int | float | str


def _decimal(valor: Numero) -> Decimal:
    if isinstance(valor, Decimal):
        return valor
    if isinstance(valor, float):
        return Decimal(repr(valor))  # repr: 0.1 -> "0.1", sem o lixo binário do float
    return Decimal(valor)


def reais(valor: Numero) -> Decimal:
    return _decimal(valor).quantize(CENTAVO, rounding=ROUND_HALF_EVEN)


def unitario(valor: Numero) -> Decimal:
    """Valor unitário e custo médio: 4 casas."""
    return _decimal(valor).quantize(_QUATRO_CASAS, rounding=ROUND_HALF_EVEN)


def quantidade(valor: Numero) -> Decimal:
    return _decimal(valor).quantize(_TRES_CASAS, rounding=ROUND_HALF_EVEN)


_REAIS_E_CENTAVOS = re.compile(
    r"^(?:(\d+)\s*(?:reais|real|conto|contos))?\s*(?:e\s*)?(?:(\d+)\s*centavos?)?$"
)
_MILHAR = re.compile(r"^\d{1,3}(\.\d{3})+$")  # 1.234 / 12.345.678


def parse_valor(valor: Numero | None) -> Decimal | None:
    """Converte o que vier (número do LLM ou texto falado) em Decimal. Vazio -> None.

    Aceita: 150,59 | 150.59 | 1.234,50 | R$ 10 | 10 reais e 50 centavos | 50 centavos.
    Lança ValueError se não for um número.
    """
    if valor is None:
        return None
    if isinstance(valor, bool):
        raise ValueError(f"valor inválido: {valor!r}")
    if isinstance(valor, (int, float, Decimal)):
        d = _decimal(valor)
        if not d.is_finite():
            raise ValueError(f"valor inválido: {valor!r}")
        return d

    texto = " ".join(str(valor).lower().replace("r$", " ").split())
    if not texto:
        return None

    m = _REAIS_E_CENTAVOS.match(texto)
    if m and (m.group(1) or m.group(2)):
        return Decimal(m.group(1) or 0) + Decimal(m.group(2) or 0) / 100

    texto = re.sub(r"\s*(reais|real|conto|contos)$", "", texto).replace(" ", "")
    if "," in texto:  # vírgula é sempre decimal (pt-BR); pontos antes dela são milhar
        texto = texto.replace(".", "").replace(",", ".")
    elif _MILHAR.match(texto):
        texto = texto.replace(".", "")
    try:
        d = Decimal(texto)
    except InvalidOperation:
        raise ValueError(f"valor inválido: {valor!r}") from None
    if not d.is_finite():
        raise ValueError(f"valor inválido: {valor!r}")
    return d


class ValoresIncoerentes(ValueError):
    """O comerciante (ou o LLM) deu unitário e total que não batem com a quantidade."""


def resolver_valores(
    qtd: Decimal | None, valor_unitario: Decimal | None, valor_total: Decimal | None
) -> tuple[Decimal | None, Decimal | None]:
    """Completa o par (unitário, total) a partir do que foi dito. Devolve (unitario, total).

    - só o unitário: total = unitário × quantidade
    - só o total: unitário = total ÷ quantidade
    - os dois: confere (tolerância de 1 centavo); se não baterem, ValoresIncoerentes
    - nenhum: (None, None)
    """
    if valor_unitario is None and valor_total is None:
        return None, None
    if valor_unitario is not None and valor_unitario < 0 or valor_total is not None and valor_total < 0:
        raise ValoresIncoerentes("valor negativo")
    if not qtd or qtd <= 0:
        # sem quantidade não dá para converter; devolve o que veio
        return (unitario(valor_unitario) if valor_unitario is not None else None,
                reais(valor_total) if valor_total is not None else None)
    if valor_total is None:
        return unitario(valor_unitario), reais(valor_unitario * qtd)
    if valor_unitario is None:
        return unitario(valor_total / qtd), reais(valor_total)
    if abs(reais(valor_unitario * qtd) - reais(valor_total)) > TOLERANCIA:
        raise ValoresIncoerentes(f"{valor_unitario} × {qtd} ≠ {valor_total}")
    return unitario(valor_unitario), reais(valor_total)
