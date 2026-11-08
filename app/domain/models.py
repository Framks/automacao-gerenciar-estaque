"""Modelos do domínio: o que a IA extrai da fala e o que fica gravado na planilha."""

from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum

from pydantic import BaseModel, Field, field_serializer, field_validator

from app.domain.dinheiro import parse_valor


class Acao(str, Enum):
    COMPRA = "compra"
    VENDA = "venda"
    CONSULTA = "consulta"
    DESFAZER = "desfazer"
    DESCONHECIDO = "desconhecido"


class Item(BaseModel):
    produto: str = ""
    quantidade: Decimal | None = None
    unidade: str | None = None
    # O LLM informa só o que foi falado: o preço por unidade OU o total. A conta é do código.
    valor_unitario: Decimal | None = None
    valor_total: Decimal | None = None

    @field_validator("produto", mode="before")
    @classmethod
    def _produto(cls, valor):
        return "" if valor is None else str(valor)

    @field_validator("quantidade", "valor_unitario", "valor_total", mode="before")
    @classmethod
    def _numero(cls, valor):
        return parse_valor(valor)

    @field_serializer("quantidade", "valor_unitario", "valor_total", when_used="json")
    def _numero_json(self, valor):
        # número no JSON (não texto): "10.000" voltaria como dez mil no parse pt-BR
        return None if valor is None else float(valor)


class Intencao(BaseModel):
    """Saída estruturada do LLM. É só isso que a IA decide; o resto é código."""

    acao: Acao
    itens: list[Item] = Field(default_factory=list)


class TipoMovimento(str, Enum):
    ENTRADA = "entrada"
    SAIDA = "saida"
    AJUSTE = "ajuste"
    AJUSTE_VALOR = "ajuste_valor"  # completa/corrige o valor de uma entrada já gravada (não mexe na quantidade)


@dataclass
class Comerciante:
    telefone: str
    nome: str
    status: str = "ativo"  # ativo | bloqueado | pendente
    criado_em: str = ""
    id: int | None = None  # no Postgres; None no Sheets

    @property
    def bloqueado(self) -> bool:
        return self.status == "bloqueado"


@dataclass
class ProdutoEstoque:
    produto: str
    unidade: str
    quantidade: Decimal
    custo_medio: Decimal | None = None
    atualizado_em: str = ""
    linha: int | None = None  # linha na planilha (None = produto novo, ainda não gravado)

    def __post_init__(self):
        _decimais(self, "quantidade", "custo_medio")


@dataclass
class Movimento:
    id: str
    data_hora: str
    tipo: TipoMovimento
    produto: str
    quantidade: Decimal  # positiva em entrada/saida; com sinal (delta) em ajuste
    unidade: str
    valor_total: Decimal | None
    valor_unitario: Decimal | None
    texto_transcrito: str
    whatsapp_msg_id: str
    referencia: str = ""  # em ajustes de "desfazer": whatsapp_msg_id da mensagem desfeita
    referencia_mov: str = ""  # em ajuste_valor: id do movimento cujo valor foi completado/corrigido
    forma_pagamento: str = "nao_informado"  # dinheiro | pix | cartao | fiado | nao_informado
    forma_pagamento_origem: str = ""  # dito | padrao | "" (não informado)
    custo_unitario: Decimal | None = None  # na saída: o custo médio na hora da venda (para o lucro)

    def __post_init__(self):
        _decimais(self, "quantidade", "valor_total", "valor_unitario", "custo_unitario")


@dataclass
class Mudanca:
    """Antes/depois de um produto, usado para montar a resposta falada."""

    produto: str
    unidade: str
    antes: Decimal
    depois: Decimal
    tipo: TipoMovimento
    quantidade: Decimal
    valor_total: Decimal | None = None

    def __post_init__(self):
        _decimais(self, "antes", "depois", "quantidade", "valor_total")


@dataclass
class Resultado:
    acao: Acao
    mudancas: list[Mudanca] = field(default_factory=list)
    movimentos: list[Movimento] = field(default_factory=list)
    estoque_alterado: list[ProdutoEstoque] = field(default_factory=list)
    consulta: list[ProdutoEstoque] = field(default_factory=list)
    nao_encontrados: list[str] = field(default_factory=list)  # produtos consultados que não existem
    problemas: list[str] = field(default_factory=list)  # frases prontas explicando por que nada foi gravado

    @property
    def ok(self) -> bool:
        return not self.problemas


def _decimais(obj, *campos: str) -> None:
    """Aceita int/float/str na construção (testes, planilha) e guarda sempre Decimal."""
    for campo in campos:
        valor = getattr(obj, campo)
        if valor is not None and not isinstance(valor, Decimal):
            setattr(obj, campo, parse_valor(valor))
