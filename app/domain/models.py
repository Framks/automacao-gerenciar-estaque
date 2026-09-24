"""Modelos do domínio: o que a IA extrai da fala e o que fica gravado na planilha."""

from dataclasses import dataclass, field
from enum import Enum

from pydantic import BaseModel, Field


class Acao(str, Enum):
    COMPRA = "compra"
    VENDA = "venda"
    CONSULTA = "consulta"
    DESFAZER = "desfazer"
    DESCONHECIDO = "desconhecido"


class Item(BaseModel):
    produto: str
    quantidade: float | None = None
    unidade: str | None = None
    valor_total: float | None = None


class Intencao(BaseModel):
    """Saída estruturada do LLM. É só isso que a IA decide; o resto é código."""

    acao: Acao
    itens: list[Item] = Field(default_factory=list)


class TipoMovimento(str, Enum):
    ENTRADA = "entrada"
    SAIDA = "saida"
    AJUSTE = "ajuste"


@dataclass
class ProdutoEstoque:
    produto: str
    unidade: str
    quantidade: float
    custo_medio: float | None = None
    atualizado_em: str = ""
    linha: int | None = None  # linha na planilha (None = produto novo, ainda não gravado)


@dataclass
class Movimento:
    id: str
    data_hora: str
    tipo: TipoMovimento
    produto: str
    quantidade: float  # positiva em entrada/saida; com sinal (delta) em ajuste
    unidade: str
    valor_total: float | None
    valor_unitario: float | None
    texto_transcrito: str
    whatsapp_msg_id: str
    referencia: str = ""  # em ajustes de "desfazer": whatsapp_msg_id da mensagem desfeita


@dataclass
class Mudanca:
    """Antes/depois de um produto, usado para montar a resposta falada."""

    produto: str
    unidade: str
    antes: float
    depois: float
    tipo: TipoMovimento
    quantidade: float
    valor_total: float | None = None


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
