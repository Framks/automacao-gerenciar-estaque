"""Templates das respostas faladas. Frases curtas e simples, pensadas para virar áudio."""

from decimal import ROUND_HALF_EVEN, Decimal

from app.domain.dinheiro import parse_valor, reais
from app.domain.models import Acao, Resultado, TipoMovimento
from app.domain.units import unidade_falada

MAX_ITENS_CONSULTA = 10

AJUDA = (
    "Você pode falar, por exemplo: comprei 10 quilos de arroz por 50 reais. "
    "Ou: vendi 2 quilos de feijão. Ou: quanto arroz eu tenho?"
)


def fmt_num(valor: Decimal | float | int) -> str:
    valor = parse_valor(valor).quantize(Decimal("0.001"), rounding=ROUND_HALF_EVEN)
    if valor == valor.to_integral_value():
        return str(int(valor))
    return f"{valor:f}".rstrip("0").replace(".", ",")


def fmt_reais(valor: Decimal | float | int) -> str:
    centavos_total = int(reais(parse_valor(valor)) * 100)
    inteiros, centavos = divmod(centavos_total, 100)
    partes = []
    if inteiros:
        partes.append(f"{inteiros} {'real' if inteiros == 1 else 'reais'}")
    if centavos:
        partes.append(f"{centavos} {'centavo' if centavos == 1 else 'centavos'}")
    return " e ".join(partes) or "0 reais"


def _qtd(quantidade: Decimal, unidade: str) -> str:
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


# --- conversa (F2): perguntas, completar, despesas -------------------------------------------------------

def qtd(quantidade: Decimal, unidade: str) -> str:
    return _qtd(quantidade, unidade)


def preco_unitario(valor: Decimal, unidade: str) -> str:
    singular = unidade_falada(unidade, 1)
    return f"{fmt_reais(valor)} {'a' if unidade in ('un', 'cx', 'duzia') else 'o'} {singular}"


def descrever_item(tipo: str, produto: str, quantidade: Decimal | None, unidade: str | None,
                   total: Decimal | None) -> str:
    """"uma compra de 10 quilos de feijão por 100 reais" """
    artigo = "uma compra" if tipo == "compra" else "uma venda"
    partes = [artigo]
    if quantidade:
        partes.append(f"de {_qtd(quantidade, unidade or 'un')} de {produto}")
    else:
        partes.append(f"de {produto}")
    if total is not None:
        partes.append(f"por {fmt_reais(total)}")
    return " ".join(partes)


def resumo_entrada(quantidade: Decimal, unidade: str, produto: str) -> str:
    """"os 10 quilos de feijão" """
    return f"{'o' if quantidade == 1 else 'os'} {_qtd(quantidade, unidade)} de {produto}"


def pergunta_valor(tipo: str, produto: str) -> str:
    if tipo == "venda":
        return f"Por quanto você vendeu o {produto}?"
    return f"Quanto você pagou no {produto}? Pode falar o preço do quilo, da unidade ou o total."


def pergunta_quantidade(produto: str, unidade: str | None) -> str:
    if unidade:
        return f"Quantos {unidade_falada(unidade, 2)} de {produto}?"
    return f"Quanto de {produto}? Fale a quantidade e a unidade, por exemplo: 5 quilos."


def pergunta_unidade(produto: str) -> str:
    return f"O {produto} é em quilo, unidade ou litro?"


def pergunta_categoria(valor: Decimal) -> str:
    return f"Os {fmt_reais(valor)} foram gastos com quê?"


def pergunta_valor_despesa() -> str:
    return "Quanto foi o gasto?"


def valor_completado(produto: str, unidade: str, quantidade: Decimal, unitario: Decimal, total: Decimal,
                     custo: Decimal | None, tipo: str) -> str:
    if tipo == "venda":
        return f"Certo! A venda de {_qtd(quantidade, unidade)} de {produto} ficou em {fmt_reais(total)}."
    frase = (f"Certo! O {produto} saiu a {preco_unitario(unitario, unidade)}, "
             f"{fmt_reais(total)} no total.")
    if custo is not None:
        frase += f" O custo do {produto} agora é {preco_unitario(custo, unidade)}."
    return frase


def quantidade_corrigida(produto: str, unidade: str, antes: Decimal, depois: Decimal) -> str:
    return f"Corrigido! {produto.capitalize()}: tinha {_qtd(antes, unidade)}, agora tem {_qtd(depois, unidade)}."


def despesa_anotada(valor: Decimal, categoria_falada: str | None, descricao: str) -> str:
    sobre = f" com {descricao}" if descricao else (f" com {categoria_falada}" if categoria_falada else "")
    return f"Anotei um gasto de {fmt_reais(valor)}{sobre}."


def retirada_anotada(valor: Decimal) -> str:
    return f"Anotei uma retirada de {fmt_reais(valor)} do caixa."


def despesa_completada(descricao: str, categoria_falada: str) -> str:
    return f"Certo! Anotei que foi com {descricao or categoria_falada}."


def gastos(total: Decimal, quantidade: int, periodo: str, maiores: list[tuple[str, Decimal]],
           categoria_falada: str | None = None) -> str:
    quando = {"hoje": "hoje", "semana": "nos últimos 7 dias"}.get(periodo, "este mês")
    sobre = f" com {categoria_falada}" if categoria_falada else ""
    if not quantidade:
        return f"Você não anotou gastos{sobre} {quando}."
    frase = f"Você gastou {fmt_reais(total)}{sobre} {quando}."
    if maiores and not categoria_falada and len(maiores) > 1:
        frase += " Os maiores: " + _juntar([f"{nome}, {fmt_reais(v)}" for nome, v in maiores]) + "."
    return frase


def lancamento_desfeito(valor: Decimal, tipo: str) -> str:
    return f"Pronto, desfiz {'o gasto' if tipo == 'despesa' else 'a retirada'} de {fmt_reais(valor)}."


CANCELADO = "Tudo bem, deixei como estava."
NADA_PARA_CONFIRMAR = "Não tem nada esperando confirmação."
NADA_PENDENTE = "Não tem nenhuma anotação esperando dado."
CONVERSA = "Oi! Estou aqui para anotar seu estoque e seus gastos. " + AJUDA
CORRIGIR_PRODUTO = "Para trocar o produto, diga: desfazer. E depois fale a anotação de novo."
NAO_ACHEI_ANOTACAO = "Não achei a anotação para corrigir."
DESPESAS_DESLIGADAS = "Ainda não estou anotando gastos. " + AJUDA
