"""Operações v2: o que o LLM extrai de uma fala, e a especificação de campos de cada tipo.

O LLM só diz O QUE foi falado. Quem decide se está completo é o código, com a especificação abaixo:
- campo obrigatório: sem ele não grava; vira pendência e o bot pergunta;
- campo desejável: grava assim mesmo; vira pendência para completar depois.
"""

from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_serializer, field_validator

from app.domain.dinheiro import parse_valor
from app.domain.models import Acao, Intencao, Item, ProdutoEstoque
from app.domain.units import chave_produto, normalizar_unidade


class TipoOperacao(str, Enum):
    COMPRA = "compra"
    VENDA = "venda"
    DESPESA = "despesa"  # gasto que não é mercadoria de estoque: luz, aluguel, frete...
    RETIRADA = "retirada"  # dinheiro que o dono tirou do caixa para ele
    COMPLETAR = "completar"  # responde uma pendência (ex.: o valor que faltou)
    CORRIGIR = "corrigir"  # muda um dado de uma anotação já feita
    CONSULTA = "consulta"
    DESFAZER = "desfazer"
    RECEBIMENTO = "recebimento"  # cliente pagou (parte d)o fiado
    PERDOAR = "perdoar"  # esquecer a dívida de um cliente
    CONFIGURAR = "configurar"  # o comerciante muda uma preferência dele (horário do fechamento...)
    CONFIRMAR = "confirmar"  # "sim" para uma pergunta de confirmação
    CANCELAR = "cancelar"  # "não", "deixa pra lá", "não sei o valor"
    CONVERSA = "conversa"  # cumprimento, dúvida, qualquer outra coisa


class Operacao(BaseModel):
    tipo: TipoOperacao
    itens: list[Item] = Field(default_factory=list)  # compra, venda, consulta de produtos
    valor: Decimal | None = None  # despesa, retirada
    categoria: str | None = None  # despesa
    descricao: str | None = None  # despesa
    pendencia_id: int | None = None  # completar, confirmar, cancelar
    movimento_ref: str | None = None  # corrigir: "ultimo" ou o id de um movimento do contexto
    campos: dict[str, Any] = Field(default_factory=dict)  # completar/corrigir: os dados novos
    assunto: str | None = None  # consulta: "estoque" (padrão), "gastos", "caixa" ou "fiado"
    periodo: str | None = None  # consulta de gastos/caixa: "hoje", "ontem", "semana", "mes", "dia:3", "mes:10"...
    formato: str | None = None  # consulta de caixa: resumo | itens | por_dia | grafico | planilha
    forma_pagamento: str | None = None  # compra, venda, despesa, recebimento: como foi pago (só se foi falado)
    cliente: str | None = None  # venda fiado, recebimento, perdoar, consulta de fiado
    tudo: bool = False  # recebimento: "acertou tudo", "pagou o que devia"
    dias: int | None = None  # consulta de fiado: "quem deve há mais de 30 dias"
    confianca: float = 1.0

    @field_validator("forma_pagamento", mode="before")
    @classmethod
    def _forma(cls, valor):
        from app.domain.pagamento import NAO_INFORMADO, normalizar_pagamento

        forma = normalizar_pagamento(valor)
        return None if forma in (None, NAO_INFORMADO) else forma

    @field_validator("tudo", mode="before")
    @classmethod
    def _tudo(cls, valor):
        return bool(valor)

    @field_validator("dias", mode="before")
    @classmethod
    def _dias(cls, valor):
        try:
            return int(float(valor)) if valor not in (None, "") else None
        except (TypeError, ValueError):
            return None

    @field_validator("valor", mode="before")
    @classmethod
    def _numero(cls, valor):
        return parse_valor(valor)

    @field_serializer("valor", when_used="json")
    def _numero_json(self, valor):
        return None if valor is None else float(valor)

    @field_serializer("campos", when_used="json")
    def _campos_json(self, campos):
        return {k: float(v) if isinstance(v, Decimal) else v for k, v in campos.items()}

    @field_validator("tipo", mode="before")
    @classmethod
    def _tipo(cls, valor):
        if isinstance(valor, Enum):
            return valor.value
        return str(valor).strip().lower() if valor is not None else valor

    @field_validator("confianca", mode="before")
    @classmethod
    def _confianca(cls, valor):
        if valor is None:
            return 1.0
        return min(max(float(valor), 0.0), 1.0)


class RespostaLLM(BaseModel):
    operacoes: list[Operacao] = Field(default_factory=list)


# --- especificação de campos -------------------------------------------------------------------------------

@dataclass(frozen=True)
class Especificacao:
    obrigatorios: tuple[str, ...]
    desejaveis: tuple[str, ...] = ()


ESPEC: dict[TipoOperacao, Especificacao] = {
    TipoOperacao.COMPRA: Especificacao(obrigatorios=("produto", "quantidade"), desejaveis=("valor", "unidade")),
    TipoOperacao.VENDA: Especificacao(obrigatorios=("produto", "quantidade"), desejaveis=("valor",)),
    TipoOperacao.DESPESA: Especificacao(obrigatorios=("valor",), desejaveis=("categoria",)),
    TipoOperacao.RETIRADA: Especificacao(obrigatorios=("valor",)),
    TipoOperacao.RECEBIMENTO: Especificacao(obrigatorios=("cliente", "valor")),
    TipoOperacao.PERDOAR: Especificacao(obrigatorios=("cliente",)),
    TipoOperacao.COMPLETAR: Especificacao(obrigatorios=("campos",)),
    TipoOperacao.CORRIGIR: Especificacao(obrigatorios=("movimento_ref", "campos")),
}


@dataclass
class FaltaItem:
    indice: int
    produto: str
    obrigatorios: list[str] = field(default_factory=list)
    desejaveis: list[str] = field(default_factory=list)


@dataclass
class Avaliacao:
    obrigatorios_faltando: list[str] = field(default_factory=list)  # da operação (despesa: valor...)
    desejaveis_faltando: list[str] = field(default_factory=list)
    itens: list[FaltaItem] = field(default_factory=list)  # o que falta em cada item (compra/venda)

    @property
    def pronta(self) -> bool:
        return not self.obrigatorios_faltando and not any(i.obrigatorios for i in self.itens)


def _tem_valor(item: Item) -> bool:
    return item.valor_unitario is not None or item.valor_total is not None


def avaliar(op: Operacao, estoque: dict[str, ProdutoEstoque]) -> Avaliacao:
    """O que falta numa operação, pela ESPEC. Função pura."""
    av = Avaliacao()
    espec = ESPEC.get(op.tipo)
    if espec is None:
        return av

    if op.tipo in (TipoOperacao.COMPRA, TipoOperacao.VENDA):
        if not op.itens:
            av.obrigatorios_faltando.append("produto")
        for i, item in enumerate(op.itens):
            falta = FaltaItem(i, item.produto)
            if not item.produto.strip():
                falta.obrigatorios.append("produto")
            if not item.quantidade or item.quantidade <= 0:
                falta.obrigatorios.append("quantidade")
            if "valor" in espec.desejaveis and not _tem_valor(item):
                falta.desejaveis.append("valor")
            if ("unidade" in espec.desejaveis and not normalizar_unidade(item.unidade)
                    and chave_produto(item.produto) not in estoque):
                falta.desejaveis.append("unidade")
            if falta.obrigatorios or falta.desejaveis:
                av.itens.append(falta)
        return av

    if op.tipo in (TipoOperacao.DESPESA, TipoOperacao.RETIRADA):
        if op.valor is None or op.valor <= 0:
            av.obrigatorios_faltando.append("valor")
        if "categoria" in espec.desejaveis and not (op.categoria and op.categoria != "outros") and not op.descricao:
            av.desejaveis_faltando.append("categoria")
        return av

    if op.tipo == TipoOperacao.COMPLETAR and not op.campos:
        av.obrigatorios_faltando.append("campos")
    if op.tipo == TipoOperacao.CORRIGIR:
        if not op.movimento_ref:
            av.obrigatorios_faltando.append("movimento_ref")
        if not op.campos:
            av.obrigatorios_faltando.append("campos")
    return av


def de_intencao(intencao: Intencao) -> RespostaLLM:
    """Adaptador da saída v1 (uma ação por mensagem) para operações v2."""
    mapa = {
        Acao.COMPRA: TipoOperacao.COMPRA, Acao.VENDA: TipoOperacao.VENDA, Acao.CONSULTA: TipoOperacao.CONSULTA,
        Acao.DESFAZER: TipoOperacao.DESFAZER, Acao.DESCONHECIDO: TipoOperacao.CONVERSA,
    }
    return RespostaLLM(operacoes=[Operacao(tipo=mapa[intencao.acao], itens=intencao.itens)])


def para_intencao(op: Operacao) -> Intencao:
    """Compra/venda/consulta v2 -> Intencao do domínio de estoque (inventory.aplicar)."""
    acao = {TipoOperacao.COMPRA: Acao.COMPRA, TipoOperacao.VENDA: Acao.VENDA,
            TipoOperacao.CONSULTA: Acao.CONSULTA}[op.tipo]
    return Intencao(acao=acao, itens=op.itens)
