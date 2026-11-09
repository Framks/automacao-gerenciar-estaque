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


def montar(resultado: Resultado, extra: str = "") -> str:
    """`extra`: frase sobre o pagamento/fiado, que entra antes do "Se estiver errado, diga: desfazer"."""
    texto = _montar(resultado)
    if extra and resultado.ok and texto.startswith("Anotado!"):
        sufixo = " Se estiver errado, diga: desfazer."
        texto = texto.removesuffix(sufixo) + " " + extra + sufixo
    return texto


def _montar(resultado: Resultado) -> str:
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


# --- meio de pagamento e fiado (F6) ----------------------------------------------------------------------

def _falada(forma: str) -> str:
    from app.domain.pagamento import FALADA

    return FALADA.get(forma, forma)


def frase_pagamento(tipo: str, forma: str) -> str:
    if forma not in ("dinheiro", "pix", "cartao"):
        return ""
    return f"{'Recebido' if tipo == 'venda' else 'Pago'} {_falada(forma)}."


def pergunta_pagamento(formas) -> str:
    from app.domain.pagamento import lista_falada

    return f"Foi em {lista_falada(formas)}?"


def forma_alterada(forma: str) -> str:
    from app.domain.pagamento import NOME

    return f"Certo! Mudei o pagamento para {NOME.get(forma, forma)}."


def ja_estava(forma: str) -> str:
    from app.domain.pagamento import NOME

    return f"Já estava anotado como {NOME.get(forma, forma)}."


def fiado_anotado(nome: str, saldo: Decimal) -> str:
    return f"Fiado para {nome}, que agora deve {fmt_reais(saldo)}."


def fiado_estornado(nome: str, saldo: Decimal) -> str:
    if saldo > 0:
        return f"Tirei do fiado de {nome}, que agora deve {fmt_reais(saldo)}."
    return f"Tirei do fiado de {nome}, que não deve mais nada."


def fiado_ajustado(nome: str, saldo: Decimal) -> str:
    return f"A conta de {nome} no fiado agora é {fmt_reais(saldo)}."


def pergunta_valor_fiado(produto: str) -> str:
    return f"Quanto ficou a venda fiado de {produto}?"


def pergunta_cliente_parecido(nome: str, sugestoes: list[str]) -> str:
    if len(sugestoes) == 1:
        return f"Você quis dizer {sugestoes[0]}, que já está no fiado? Diga sim, ou diga: é cliente novo."
    return f"É {_juntar(sugestoes).replace(' e ', ' ou ')}? Fale o nome certo, ou diga: é cliente novo."


def pergunta_qual_cliente(sugestoes: list[str]) -> str:
    return f"Foi {_juntar(sugestoes).replace(' e ', ' ou ')}?"


def pagamento_fiado(nome: str, valor: Decimal, forma: str, saldo: Decimal) -> str:
    meio = f" {_falada(forma)}" if forma in ("dinheiro", "pix", "cartao") else ""
    frase = f"Anotei: {nome} pagou {fmt_reais(valor)}{meio}."
    if saldo > 0:
        return frase + f" Ainda deve {fmt_reais(saldo)}."
    return frase + " Agora não deve mais nada."


def pergunta_quanto_pagou(nome: str) -> str:
    return f"Quanto {nome} pagou?"


def confirmar_quitacao(nome: str, saldo: Decimal) -> str:
    return (f"{nome} deve só {fmt_reais(saldo)}. Anoto o pagamento de {fmt_reais(saldo)}, que quita tudo? "
            "Diga sim ou não.")


def nao_deve(nome: str) -> str:
    return f"{nome} não está devendo nada."


def cliente_nao_achado(nome: str, sugestoes: list[str]) -> str:
    frase = f"Não achei {nome} no fiado."
    if sugestoes:
        frase += f" Seria {_juntar(sugestoes).replace(' e ', ' ou ')}?"
    return frase


def confirmar_perdao(nome: str, saldo: Decimal) -> str:
    return f"Esquecer a dívida de {fmt_reais(saldo)} de {nome}? Diga sim ou não."


def perdoado(nome: str, valor: Decimal) -> str:
    return f"Pronto, esqueci a dívida de {fmt_reais(valor)} de {nome}."


def saldo_cliente(nome: str, saldo: Decimal, desde: str, ultimas: list[tuple[str, Decimal]]) -> str:
    frase = f"{nome} deve {fmt_reais(saldo)}"
    if desde:
        frase += f", desde o dia {desde[8:10]}/{desde[5:7]}"
    frase += "."
    if ultimas:
        frase += " Últimas compras fiado: " + _juntar([f"dia {d[8:10]}/{d[5:7]}, {fmt_reais(v)}"
                                                       for d, v in ultimas]) + "."
    return frase


def devedores(lista: list[tuple[str, Decimal]], total: Decimal, dias: int | None = None) -> str:
    ha = f" há mais de {dias} dias" if dias else ""
    if not lista:
        return f"Ninguém está devendo{ha}."
    partes = [f"{nome}, {fmt_reais(v)}" for nome, v in lista[:MAX_ITENS_CONSULTA]]
    frase = f"Estão devendo{ha}: " + "; ".join(partes)
    resto = len(lista) - MAX_ITENS_CONSULTA
    if resto > 0:
        frase += f"; e mais {resto} clientes"
    return frase + f". Total: {fmt_reais(total)}."


def pagamento_desfeito(nome: str, valor: Decimal, saldo: Decimal, tipo: str = "pagamento") -> str:
    o_que = "o pagamento" if tipo == "pagamento" else "o perdão da dívida"
    return f"Pronto, desfiz {o_que} de {fmt_reais(valor)} de {nome}. Agora deve {fmt_reais(saldo)}."


PERGUNTA_CLIENTE_FIADO = "Fiado pra quem?"
PERGUNTA_QUEM_PAGOU = "Quem pagou?"
FIADO_DESLIGADO = "O fiado está desligado para você. Fale com o suporte para ligar."
FIADO_PRECISA_VALOR = "Para pôr no fiado, preciso do valor da venda. Diga, por exemplo: a venda foi 20 reais."
NAO_ENTENDI_PAGAMENTO = "Não entendi como foi pago."


# --- preferências do comerciante (fechamento do caixa) ---------------------------------------------------

def fechamento_ligado(hhmm: str) -> str:
    from app.domain.periodos import falar_horario

    return (f"Combinado: todo dia às {falar_horario(hhmm)} eu te mando o fechamento do caixa. "
            "Pode chegar até 15 minutos depois.")


def pergunta_manha_noite(hora: int) -> str:
    return f"{hora} da manhã ou da noite?"


def config_atual(horario: str, mensal: bool, formato: str, pagamento_padrao: str) -> str:
    from app.domain.pagamento import NOME
    from app.domain.periodos import falar_horario

    partes = [f"O fechamento do dia chega às {falar_horario(horario)}" if horario
              else "O fechamento do dia está desligado"]
    partes.append("o do mês chega no dia 1" if mensal else "o do mês está desligado")
    frase = ", ".join(partes) + "."
    if horario:
        frase += " Ele vem " + ("resumido." if formato == "resumo" else "com a lista do que entrou e saiu.")
    if pagamento_padrao and pagamento_padrao != "nao_informado":
        frase += f" Quando você não fala o pagamento, eu anoto {NOME.get(pagamento_padrao, pagamento_padrao)}."
    return frase + " Para mudar, diga por exemplo: me manda o fechamento todo dia às 8 da noite."


FECHAMENTO_DESLIGADO = "Pronto, não vou mais mandar o fechamento do dia."
FECHAMENTO_MENSAL_LIGADO = "Combinado: no dia 1 de cada mês eu te mando o fechamento do mês anterior."
FECHAMENTO_MENSAL_DESLIGADO = "Pronto, não vou mais mandar o fechamento do mês."
FECHAMENTO_FORMATO_ITENS = "Certo: o fechamento vai vir com a lista do que entrou e saiu."
FECHAMENTO_FORMATO_RESUMO = "Certo: o fechamento vai vir resumido."
HORARIO_NAO_ENTENDI = "Não entendi o horário. Diga, por exemplo: às 8 da noite, ou às 19 e 30."
CONFIG_NAO_PERMITIDA = "Isso eu não consigo mudar por aqui. Fale com o suporte."
PAGAMENTO_PADRAO_FIADO = "O fiado precisa do nome do cliente, então não dá para ser o padrão."


def pagamento_padrao_definido(forma: str) -> str:
    from app.domain.pagamento import NOME

    if forma == "nao_informado":
        return "Certo: quando você não falar o pagamento, fica sem meio anotado."
    return f"Certo: quando você não falar o pagamento, eu anoto {NOME.get(forma, forma)}."
