"""Templates das respostas faladas. Frases curtas e simples, pensadas para virar áudio."""

from app.domain.models import Acao, Resultado, TipoMovimento
from app.domain.units import unidade_falada

MAX_ITENS_CONSULTA = 10

AJUDA = (
    "Você pode falar, por exemplo: comprei 10 quilos de arroz por 50 reais. "
    "Ou: vendi 2 quilos de feijão. Ou: quanto arroz eu tenho?"
)


def fmt_num(valor: float) -> str:
    valor = round(valor, 3)
    if valor == int(valor):
        return str(int(valor))
    return f"{valor:.3f}".rstrip("0").replace(".", ",")


def fmt_reais(valor: float) -> str:
    centavos_total = round(valor * 100)
    reais, centavos = divmod(centavos_total, 100)
    partes = []
    if reais:
        partes.append(f"{reais} {'real' if reais == 1 else 'reais'}")
    if centavos:
        partes.append(f"{centavos} {'centavo' if centavos == 1 else 'centavos'}")
    return " e ".join(partes) or "0 reais"


def _qtd(quantidade: float, unidade: str) -> str:
    return f"{fmt_num(quantidade)} {unidade_falada(unidade, quantidade)}"


def boas_vindas(nome: str | None) -> str:
    saudacao = f"Olá, {nome}!" if nome else "Olá!"
    return f"{saudacao} Seu cadastro foi feito. É só me mandar um áudio dizendo o que comprou ou vendeu."


def montar(resultado: Resultado) -> str:
    if resultado.problemas:
        return "Não anotei nada. " + " ".join(resultado.problemas)

    if resultado.acao == Acao.DESCONHECIDO:
        return "Não entendi. " + AJUDA

    if resultado.acao == Acao.CONSULTA:
        return _consulta(resultado)

    frases = []
    for m in resultado.mudancas:
        if m.tipo == TipoMovimento.AJUSTE:
            frases.append(f"{m.produto}: tinha {_qtd(m.antes, m.unidade)}, agora tem {_qtd(m.depois, m.unidade)}.")
            continue
        verbo = "Entrou" if m.tipo == TipoMovimento.ENTRADA else "Saiu"
        preco = f" por {fmt_reais(m.valor_total)}" if m.valor_total is not None else ""
        frases.append(
            f"{verbo} {_qtd(m.quantidade, m.unidade)} de {m.produto}{preco}. "
            f"Tinha {_qtd(m.antes, m.unidade)}, agora tem {_qtd(m.depois, m.unidade)}."
        )

    if resultado.acao == Acao.DESFAZER:
        return "Pronto, desfiz a última anotação. " + " ".join(frases)
    return "Anotado! " + " ".join(frases) + " Se estiver errado, diga: desfazer."


def _consulta(resultado: Resultado) -> str:
    frases = []
    produtos = resultado.consulta
    if not produtos and not resultado.nao_encontrados:
        return "Seu estoque ainda está vazio."
    if produtos:
        partes = [f"{_qtd(p.quantidade, p.unidade)} de {p.produto}" for p in produtos[:MAX_ITENS_CONSULTA]]
        frase = "Você tem " + _juntar(partes)
        resto = len(produtos) - MAX_ITENS_CONSULTA
        if resto > 0:
            frase += f", e mais {resto} produtos"
        frases.append(frase + ".")
    for nome in resultado.nao_encontrados:
        frases.append(f"Não tem {nome} anotado.")
    return " ".join(frases)


def _juntar(partes: list[str]) -> str:
    if len(partes) <= 1:
        return "".join(partes)
    return ", ".join(partes[:-1]) + " e " + partes[-1]
