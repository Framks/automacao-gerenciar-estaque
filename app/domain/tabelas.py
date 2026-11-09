"""Tabelas em texto monoespaçado para o WhatsApp (que não desenha tabela Markdown). Código puro.

O bloco entre três crases usa fonte monoespaçada no WhatsApp; num celular cabem ~34 colunas sem quebrar.
"""

from decimal import ROUND_HALF_EVEN, Decimal

from app.domain.dinheiro import reais

LARGURA_MAXIMA = 34


class TabelaLarga(ValueError):
    pass


def _milhar(inteiro: int) -> str:
    return f"{inteiro:,}".replace(",", ".")


def fmt_rs(valor: Decimal | int | float) -> str:
    """"R$ 1.234,56" """
    v = reais(Decimal(str(valor)))
    sinal = "-" if v < 0 else ""
    inteiros, centavos = divmod(int(abs(v) * 100), 100)
    return f"{sinal}R$ {_milhar(inteiros)},{centavos:02d}"


def fmt_valor(valor: Decimal | int | float) -> str:
    """"1.234,56" (sem "R$", para colunas estreitas)"""
    return fmt_rs(valor).replace("R$ ", "")


def fmt_inteiro(valor: Decimal | int | float) -> str:
    """"1.235" (reais arredondados, para a tabela do mês caber no celular)"""
    v = Decimal(str(valor)).quantize(Decimal(1), rounding=ROUND_HALF_EVEN)
    return ("-" if v < 0 else "") + _milhar(abs(int(v)))


def tabela(cabecalho: list[str] | None, linhas: list[list[str]], alinhamento: str,
           largura_maxima: int = LARGURA_MAXIMA, rodape: list[list[str]] | None = None) -> str:
    """Colunas alinhadas ("l" esquerda, "r" direita), separadas por um espaço. Lança TabelaLarga se passar do limite."""
    todas = ([cabecalho] if cabecalho else []) + linhas + (rodape or [])
    if not todas:
        return ""
    larguras = [max(len(l[i]) for l in todas) for i in range(len(alinhamento))]

    def formatar(linha: list[str]) -> str:
        partes = [c.ljust(w) if a == "l" else c.rjust(w) for c, w, a in zip(linha, larguras, alinhamento)]
        return " ".join(partes).rstrip()

    saida = [formatar(l) for l in todas]
    if rodape:
        saida.insert(len(saida) - len(rodape), "-" * min(sum(larguras) + len(larguras) - 1, largura_maxima))
    maior = max(len(l) for l in saida)
    if maior > largura_maxima:
        raise TabelaLarga(f"linha com {maior} colunas (máximo {largura_maxima})")
    return "\n".join(saida)


def bloco(texto: str) -> str:
    return f"```\n{texto}\n```" if texto else ""


def cortar(texto: str, n: int) -> str:
    return texto if len(texto) <= n else texto[: n - 1] + "…"
