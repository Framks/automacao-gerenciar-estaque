"""Relatório de caixa (dia, período, mês por dia). Toda conta é feita aqui, sobre o livro-caixa. Código puro.

Definições:
- Vendas: saídas com valor (inclui fiado) = o que foi vendido. Separadas por meio de pagamento.
- Compras: entradas com valor. Despesas e retiradas: lançamentos financeiros.
- Recebido de fiado: pagamentos de fiado no período.
- Entrou: vendas que não foram fiado + recebido de fiado. Saiu: compras + despesas + retiradas.
- Saldo: entrou − saiu. Lucro bruto estimado: vendas − custo das vendas (custo médio na hora da venda).
Valem os valores vigentes (com ajuste_valor) e o que foi desfeito fica de fora.
"""

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal

from app.domain.dinheiro import reais
from app.domain.financeiro import Lancamento
from app.domain.fiado import LancamentoFiado
from app.domain.fiado import desfeitos as fiado_desfeitos
from app.domain.inventory import valores_vigentes
from app.domain.models import Movimento, TipoMovimento
from app.domain.pagamento import NAO_INFORMADO
from app.domain.pagamento import NOME as NOME_MEIO
from app.domain.periodos import Periodo
from app.domain.replies import fmt_num, fmt_reais
from app.domain.tabelas import TabelaLarga, bloco, cortar, fmt_inteiro, fmt_rs, fmt_valor, tabela

ZERO = Decimal(0)
MAX_ITENS = 15
FORMATOS = ("lista", "resumo", "itens", "por_dia")
ABREVIA_FORMA = {"dinheiro": "din", "pix": "pix", "cartao": "cart", "fiado": "fiado", NAO_INFORMADO: "-"}


@dataclass
class LinhaDia:
    dia: date
    vendas: Decimal = ZERO
    compras: Decimal = ZERO
    despesas: Decimal = ZERO
    retiradas: Decimal = ZERO
    vendido_fiado: Decimal = ZERO
    recebido_fiado: Decimal = ZERO

    @property
    def entrou(self) -> Decimal:
        return self.vendas - self.vendido_fiado + self.recebido_fiado

    @property
    def saiu(self) -> Decimal:
        return self.compras + self.despesas + self.retiradas

    @property
    def saldo(self) -> Decimal:
        return self.entrou - self.saiu

    @property
    def vazio(self) -> bool:
        return not any([self.vendas, self.compras, self.despesas, self.retiradas, self.recebido_fiado])


@dataclass
class LinhaItem:
    produto: str
    tipo: str  # venda | compra
    unidade: str
    quantidade: Decimal = ZERO
    valor: Decimal = ZERO
    sem_valor: int = 0
    formas: list[str] = field(default_factory=list)


@dataclass
class RelatorioCaixa:
    periodo: Periodo
    vendas: Decimal = ZERO
    por_forma: dict[str, Decimal] = field(default_factory=dict)
    compras: Decimal = ZERO
    despesas: Decimal = ZERO
    despesas_por_categoria: dict[str, Decimal] = field(default_factory=dict)
    retiradas: Decimal = ZERO
    recebido_fiado: Decimal = ZERO
    custo_vendas: Decimal = ZERO
    vendas_com_custo: Decimal = ZERO
    vendas_sem_custo: int = 0
    vendas_sem_valor: int = 0
    compras_sem_valor: int = 0
    dias: list[LinhaDia] = field(default_factory=list)
    itens: list[LinhaItem] = field(default_factory=list)

    @property
    def vendido_fiado(self) -> Decimal:
        return self.por_forma.get("fiado", ZERO)

    @property
    def entrou(self) -> Decimal:
        return reais(self.vendas - self.vendido_fiado + self.recebido_fiado)

    @property
    def saiu(self) -> Decimal:
        return reais(self.compras + self.despesas + self.retiradas)

    @property
    def saldo(self) -> Decimal:
        return reais(self.entrou - self.saiu)

    @property
    def lucro_bruto(self) -> Decimal | None:
        return reais(self.vendas_com_custo - self.custo_vendas) if self.vendas_com_custo else None

    @property
    def vazio(self) -> bool:
        return all(d.vazio for d in self.dias) and not (self.vendas_sem_valor or self.compras_sem_valor)

    @property
    def dias_com_movimento(self) -> list[LinhaDia]:
        return [d for d in self.dias if not d.vazio]

    def como_dict(self) -> dict:
        """Para a API admin (valores como texto decimal)."""
        s = lambda v: str(reais(v))  # noqa: E731
        return {
            "periodo": {"inicio": self.periodo.inicio.isoformat(), "fim": self.periodo.fim.isoformat(),
                        "rotulo": self.periodo.rotulo},
            "vendas": s(self.vendas), "por_forma": {k: s(v) for k, v in self.por_forma.items()},
            "compras": s(self.compras), "despesas": s(self.despesas),
            "despesas_por_categoria": {k: s(v) for k, v in self.despesas_por_categoria.items()},
            "retiradas": s(self.retiradas), "recebido_fiado": s(self.recebido_fiado),
            "vendido_fiado": s(self.vendido_fiado), "entrou": s(self.entrou), "saiu": s(self.saiu),
            "saldo": s(self.saldo), "lucro_bruto": s(self.lucro_bruto) if self.lucro_bruto is not None else None,
            "avisos": {"compras_sem_valor": self.compras_sem_valor, "vendas_sem_valor": self.vendas_sem_valor,
                       "vendas_sem_custo": self.vendas_sem_custo},
            "dias": [{"dia": d.dia.isoformat(), "vendas": s(d.vendas), "compras": s(d.compras),
                      "despesas": s(d.despesas), "retiradas": s(d.retiradas), "vendido_fiado": s(d.vendido_fiado),
                      "recebido_fiado": s(d.recebido_fiado), "entrou": s(d.entrou), "saiu": s(d.saiu),
                      "saldo": s(d.saldo)} for d in self.dias],
            "itens": [{"produto": i.produto, "tipo": i.tipo, "unidade": i.unidade, "quantidade": str(i.quantidade),
                       "valor": s(i.valor), "sem_valor": i.sem_valor, "formas": i.formas} for i in self.itens],
        }


def _data(data_hora: str) -> date:
    return datetime.strptime(data_hora[:10], "%Y-%m-%d").date()


def calcular(periodo: Periodo, movimentos: list[Movimento], lancamentos: list[Lancamento],
             fiado: list[LancamentoFiado]) -> RelatorioCaixa:
    """`movimentos`/`lancamentos`/`fiado`: tudo desde o início do período (inclusive o que veio depois dele,
    para os ajustes valerem), em ordem cronológica."""
    r = RelatorioCaixa(periodo)
    dias = {d: LinhaDia(d) for d in periodo.dias}
    no_periodo = lambda dh: periodo.inicio <= _data(dh) <= periodo.fim  # noqa: E731

    vigentes = valores_vigentes(movimentos)
    msgs_desfeitas = {m.referencia for m in movimentos if m.tipo == TipoMovimento.AJUSTE and m.referencia}
    correcao: dict[str, Decimal] = {}  # corrigir quantidade: ajuste com referencia_mov (delta no estoque)
    for m in movimentos:
        if m.tipo == TipoMovimento.AJUSTE and m.referencia_mov:
            correcao[m.referencia_mov] = correcao.get(m.referencia_mov, ZERO) + m.quantidade

    itens: dict[tuple[str, str], LinhaItem] = {}
    for m in movimentos:
        if m.tipo not in (TipoMovimento.ENTRADA, TipoMovimento.SAIDA) or not no_periodo(m.data_hora):
            continue
        if m.whatsapp_msg_id in msgs_desfeitas:
            continue
        venda = m.tipo == TipoMovimento.SAIDA
        delta = correcao.get(m.id, ZERO)
        qtd = m.quantidade - delta if venda else m.quantidade + delta
        _, total = vigentes.get(m.id, (m.valor_unitario, m.valor_total))
        linha = dias[_data(m.data_hora)]
        item = itens.setdefault((m.produto, "venda" if venda else "compra"),
                                LinhaItem(m.produto, "venda" if venda else "compra", m.unidade))
        item.quantidade += qtd
        forma = m.forma_pagamento or NAO_INFORMADO
        if forma not in item.formas:
            item.formas.append(forma)
        if total is None:
            item.sem_valor += 1
            if venda:
                r.vendas_sem_valor += 1
            else:
                r.compras_sem_valor += 1
            continue
        item.valor += total
        if venda:
            r.vendas += total
            linha.vendas += total
            r.por_forma[forma] = r.por_forma.get(forma, ZERO) + total
            if forma == "fiado":
                linha.vendido_fiado += total
            if m.custo_unitario is not None:
                r.custo_vendas += qtd * m.custo_unitario
                r.vendas_com_custo += total
            else:
                r.vendas_sem_custo += 1
        else:
            r.compras += total
            linha.compras += total

    lanc_desfeitos = {l.referencia for l in lancamentos if l.tipo == "ajuste" and l.referencia}
    for l in lancamentos:
        if l.tipo not in ("despesa", "retirada") or l.id in lanc_desfeitos or not no_periodo(l.data_hora):
            continue
        linha = dias[_data(l.data_hora)]
        if l.tipo == "despesa":
            r.despesas += l.valor
            linha.despesas += l.valor
            r.despesas_por_categoria[l.categoria] = r.despesas_por_categoria.get(l.categoria, ZERO) + l.valor
        else:
            r.retiradas += l.valor
            linha.retiradas += l.valor

    f_desfeitos = fiado_desfeitos(fiado)
    for l in fiado:
        if l.tipo == "pagamento" and l.id not in f_desfeitos and no_periodo(l.data_hora):
            r.recebido_fiado += -l.valor
            dias[_data(l.data_hora)].recebido_fiado += -l.valor

    r.custo_vendas = reais(r.custo_vendas)
    r.dias = list(dias.values())
    r.itens = sorted(itens.values(), key=lambda i: (i.tipo != "venda", -i.valor, i.produto))
    return r


# --- texto para o WhatsApp ---------------------------------------------------------------------------------

def _avisos(r: RelatorioCaixa) -> list[str]:
    avisos = []
    if r.compras_sem_valor:
        avisos.append(f"⚠ {r.compras_sem_valor} compra{'s' if r.compras_sem_valor > 1 else ''} sem valor")
    if r.vendas_sem_valor:
        avisos.append(f"⚠ {r.vendas_sem_valor} venda{'s' if r.vendas_sem_valor > 1 else ''} sem valor")
    if r.vendas_sem_custo:
        avisos.append(f"⚠ {r.vendas_sem_custo} venda{'s' if r.vendas_sem_custo > 1 else ''} sem custo (fora do lucro)")
    return avisos


def texto_resumo(r: RelatorioCaixa, titulo: str = "Caixa") -> str:
    if r.vazio:
        return f"{titulo}: {r.periodo.rotulo}. Não teve movimento anotado."
    linhas = [["Vendas", fmt_rs(r.vendas)]]
    for forma in ("dinheiro", "pix", "cartao", "fiado", NAO_INFORMADO):
        if r.por_forma.get(forma):
            nome = {"cartao": "cartão", NAO_INFORMADO: "sem meio"}.get(forma, forma)
            linhas.append([f"  {nome}", fmt_rs(r.por_forma[forma])])
    linhas.append(["Compras", fmt_rs(r.compras)])
    if r.despesas:
        linhas.append(["Despesas", fmt_rs(r.despesas)])
    if r.retiradas:
        linhas.append(["Retiradas", fmt_rs(r.retiradas)])
    if r.recebido_fiado:
        linhas.append(["Recebi fiado", fmt_rs(r.recebido_fiado)])
    linhas += [["Entrou", fmt_rs(r.entrou)], ["Saiu", fmt_rs(r.saiu)], ["Saldo", fmt_rs(r.saldo)]]
    if r.lucro_bruto is not None:
        linhas.append(["Lucro bruto ≈", fmt_rs(r.lucro_bruto)])
    corpo = tabela(None, linhas, "lr")
    avisos = _avisos(r)
    return f"{titulo}: {r.periodo.rotulo}\n" + bloco(corpo) + ("\n" + "\n".join(avisos) if avisos else "")


def texto_itens(r: RelatorioCaixa, tipo: str | None = "venda", limite: int = MAX_ITENS) -> str:
    itens = [i for i in r.itens if tipo is None or i.tipo == tipo]
    nome = {"venda": "Vendido", "compra": "Comprado"}.get(tipo or "", "Movimento")
    if not itens:
        return f"{nome}: {r.periodo.rotulo}. Nada anotado."
    mostrar = itens[:limite]
    linhas = []
    for i in mostrar:
        qtd = f"{fmt_num(i.quantidade)} {i.unidade}"
        valor = fmt_valor(i.valor) if i.valor or not i.sem_valor else "?"
        meio = "+".join(ABREVIA_FORMA.get(f, f) for f in i.formas[:2])
        linha = [cortar(i.produto, 10), qtd, valor]
        if tipo is None:
            linha.append(i.tipo)
        else:
            linha.append(meio)
        linhas.append(linha)
    total = sum((i.valor for i in itens), ZERO)
    cab = ["produto", "qtd", "R$", "tipo" if tipo is None else "meio"]
    try:
        corpo = tabela(cab, linhas, "lrrl", rodape=[["total", "", fmt_valor(total), ""]])
    except TabelaLarga:
        corpo = tabela(cab[:3], [l[:3] for l in linhas], "lrr", rodape=[["total", "", fmt_valor(total)]])
    texto = f"{nome}: {r.periodo.rotulo}\n" + bloco(corpo)
    if len(itens) > limite:
        texto += f"\n…e mais {len(itens) - limite} itens. Peça \"manda a planilha\" para ver tudo."
    return texto


def texto_por_dia(r: RelatorioCaixa) -> str:
    dias = r.dias_com_movimento
    if not dias:
        return f"Caixa: {r.periodo.rotulo}. Não teve movimento anotado."
    linhas = [[d.dia.strftime("%d"), fmt_inteiro(d.vendas), fmt_inteiro(d.compras),
               fmt_inteiro(d.despesas + d.retiradas), fmt_inteiro(d.saldo)] for d in dias]
    rodape = [["tot", fmt_inteiro(r.vendas), fmt_inteiro(r.compras), fmt_inteiro(r.despesas + r.retiradas),
               fmt_inteiro(r.saldo)]]
    corpo = tabela(["dia", "vendas", "compra", "gasto", "saldo"], linhas, "lrrrr", rodape=rodape)
    texto = f"Caixa: {r.periodo.rotulo} (valores em R$)\n" + bloco(corpo)
    extras = []
    if r.vendido_fiado:
        extras.append(f"Vendido fiado: {fmt_rs(r.vendido_fiado)}. Recebido de fiado: {fmt_rs(r.recebido_fiado)}.")
    if r.lucro_bruto is not None:
        extras.append(f"Lucro bruto estimado: {fmt_rs(r.lucro_bruto)}.")
    extras += _avisos(r)
    return texto + ("\n" + "\n".join(extras) if extras else "")


def _linha_lista(i: LinhaItem) -> str:
    valor = fmt_rs(i.valor) if i.valor or not i.sem_valor else "sem valor"
    if i.valor and i.sem_valor:
        valor += f" (+{i.sem_valor} sem valor)"
    meio = ""
    if i.tipo == "venda" and i.formas and i.formas != [NAO_INFORMADO]:
        meio = " (" + ", ".join(NOME_MEIO.get(f, f) for f in i.formas if f != NAO_INFORMADO) + ")"
    return f"• {i.produto} — {fmt_num(i.quantidade)} {i.unidade} — {valor}{meio}"


def texto_lista(r: RelatorioCaixa, titulo: str = "Fechamento do caixa") -> str:
    """O fechamento: uma lista simples com nome, quantidade e valor de tudo que entrou e saiu no período."""
    if r.vazio:
        return f"{titulo}: {r.periodo.rotulo}. Não teve movimento anotado."
    partes = [f"{titulo}: {r.periodo.rotulo}"]
    for tipo, nome, total in (("venda", "Vendido", r.vendas), ("compra", "Comprado", r.compras)):
        itens = [i for i in r.itens if i.tipo == tipo]
        if itens:
            partes.append(f"{nome}:\n" + "\n".join(_linha_lista(i) for i in itens) + f"\nTotal {nome.lower()}: {fmt_rs(total)}")
    extras = []
    if r.despesas:
        extras.append(f"Despesas: {fmt_rs(r.despesas)}")
    if r.retiradas:
        extras.append(f"Retiradas: {fmt_rs(r.retiradas)}")
    if r.recebido_fiado:
        extras.append(f"Recebido de fiado: {fmt_rs(r.recebido_fiado)}")
    extras.append(f"Saldo do caixa: {fmt_rs(r.saldo)}")
    partes.append("\n".join(extras))
    avisos = _avisos(r)
    if avisos:
        partes.append("\n".join(avisos))
    return "\n\n".join(partes)


def texto(r: RelatorioCaixa, formato: str = "resumo", titulo: str = "Caixa") -> str:
    if formato == "lista":
        return texto_lista(r)
    if formato == "itens":
        return texto_itens(r, "venda")
    if formato == "por_dia":
        return texto_por_dia(r)
    return texto_resumo(r, titulo)


def fala(r: RelatorioCaixa) -> str:
    """O resumo para a voz (a tabela vai só no texto)."""
    if r.vazio:
        return f"Não teve movimento anotado {r.periodo.quando}."
    quando = r.periodo.quando
    partes = [f"{quando[0].upper()}{quando[1:]}, você vendeu {fmt_reais(r.vendas)}"]
    if r.vendido_fiado:
        partes.append(f"sendo {fmt_reais(r.vendido_fiado)} fiado")
    partes.append(f"comprou {fmt_reais(r.compras)}")
    if r.despesas or r.retiradas:
        partes.append(f"gastou {fmt_reais(r.despesas + r.retiradas)}")
    frase = ", ".join(partes) + "."
    if r.saldo >= 0:
        frase += f" Sobrou {fmt_reais(r.saldo)} no caixa."
    else:
        frase += f" Saiu {fmt_reais(-r.saldo)} a mais do que entrou."
    if r.compras_sem_valor:
        frase += f" Tem {r.compras_sem_valor} compra{'s' if r.compras_sem_valor > 1 else ''} sem valor."
    return frase + " Mandei a tabela por escrito."


def texto_curto_template(r: RelatorioCaixa) -> list[str]:
    """Parâmetros do template de fechamento: período, vendas, compras, saldo."""
    return [r.periodo.rotulo, fmt_rs(r.vendas), fmt_rs(r.compras), fmt_rs(r.saldo)]



# --- planilha do período (o que o comerciante recebe como arquivo) ------------------------------------------

COLUNAS_PLANILHA = ["data", "hora", "tipo", "produto_ou_descricao", "quantidade", "unidade", "valor",
                    "meio_pagamento", "cliente"]


def linhas_planilha(periodo: Periodo, movimentos: list[Movimento], lancamentos: list[Lancamento],
                    fiado: list[LancamentoFiado]) -> list[dict]:
    """Uma linha por venda, compra, despesa, retirada e pagamento de fiado do período (o que foi desfeito sai)."""
    no_periodo = lambda dh: periodo.inicio <= _data(dh) <= periodo.fim  # noqa: E731
    vigentes = valores_vigentes(movimentos)
    msgs_desfeitas = {m.referencia for m in movimentos if m.tipo == TipoMovimento.AJUSTE and m.referencia}
    correcao: dict[str, Decimal] = {}
    for m in movimentos:
        if m.tipo == TipoMovimento.AJUSTE and m.referencia_mov:
            correcao[m.referencia_mov] = correcao.get(m.referencia_mov, ZERO) + m.quantidade
    f_desfeitos = fiado_desfeitos(fiado)
    cliente_da_venda = {l.movimento_ref: l.cliente_nome for l in fiado
                        if l.tipo == "divida" and l.id not in f_desfeitos}

    linhas = []
    for m in movimentos:
        if m.tipo not in (TipoMovimento.ENTRADA, TipoMovimento.SAIDA) or not no_periodo(m.data_hora) \
                or m.whatsapp_msg_id in msgs_desfeitas:
            continue
        venda = m.tipo == TipoMovimento.SAIDA
        delta = correcao.get(m.id, ZERO)
        _, total = vigentes.get(m.id, (m.valor_unitario, m.valor_total))
        linhas.append({"data_hora": m.data_hora, "tipo": "venda" if venda else "compra", "produto_ou_descricao": m.produto,
                       "quantidade": m.quantidade - delta if venda else m.quantidade + delta, "unidade": m.unidade,
                       "valor": total, "meio_pagamento": m.forma_pagamento or NAO_INFORMADO,
                       "cliente": cliente_da_venda.get(m.id, "")})
    lanc_desfeitos = {l.referencia for l in lancamentos if l.tipo == "ajuste" and l.referencia}
    for l in lancamentos:
        if l.tipo in ("despesa", "retirada") and l.id not in lanc_desfeitos and no_periodo(l.data_hora):
            linhas.append({"data_hora": l.data_hora, "tipo": l.tipo,
                           "produto_ou_descricao": l.descricao or l.categoria, "quantidade": None, "unidade": "",
                           "valor": l.valor, "meio_pagamento": l.forma_pagamento or NAO_INFORMADO, "cliente": ""})
    for l in fiado:
        if l.tipo in ("pagamento", "perdao") and l.id not in f_desfeitos and no_periodo(l.data_hora):
            linhas.append({"data_hora": l.data_hora,
                           "tipo": "pagamento de fiado" if l.tipo == "pagamento" else "dívida perdoada",
                           "produto_ou_descricao": "", "quantidade": None, "unidade": "", "valor": -l.valor,
                           "meio_pagamento": l.forma_pagamento or NAO_INFORMADO, "cliente": l.cliente_nome})
    linhas.sort(key=lambda x: x["data_hora"])
    for x in linhas:
        dh = x.pop("data_hora")
        x["data"], x["hora"] = f"{dh[8:10]}/{dh[5:7]}/{dh[:4]}", dh[11:16]
    return linhas
