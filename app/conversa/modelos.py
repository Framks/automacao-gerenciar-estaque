"""Estado da conversa e o plano de escrita de uma mensagem.

O atendimento (app/conversa/atendimento.py) é código puro: recebe o estado (estoque, pendências, livro-caixa
recente) e devolve um `Plano` com tudo que deve ser gravado, mais o texto da resposta. A loja grava o plano
numa transação só.
"""

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from app.domain.fiado import Cliente, LancamentoFiado
from app.domain.financeiro import AlteracaoLancamento, CATEGORIAS, Lancamento
from app.domain.models import Movimento, ProdutoEstoque
from app.domain.pagamento import FORMAS, NAO_INFORMADO


@dataclass
class Pendencia:
    """Algo que ficou faltando. `operacao` é a operação v2 (dict) como foi entendida.

    - completar com `movimento_ref`: a entrada/saída já foi gravada; falta um dado desejável (valor);
    - completar sem `movimento_ref`/`lancamento_ref`: a operação ainda não foi gravada (faltou um obrigatório);
    - completar com `lancamento_ref`: a despesa foi gravada; falta a categoria;
    - confirmacao: nada gravado; `operacao` espera um "sim".
    """

    tipo: str  # completar | confirmacao
    operacao: dict[str, Any]
    campos_faltando: list[str]
    resumo: str
    pergunta: str
    expira_em: str
    criado_em: str
    movimento_ref: str = ""
    lancamento_ref: str = ""
    item: int = 0  # índice do item da operação (compra/venda com vários itens)
    status: str = "aberta"
    wa_msg_id: str = ""
    id: int | None = None  # None até ser gravada
    chave_local: str = ""  # identifica a pendência nova dentro do plano, antes de ter id


@dataclass
class FechamentoPendencia:
    id: int
    status: str  # resolvida | cancelada | expirada


@dataclass
class AlteracaoMovimento:
    """Mudar o meio de pagamento é dado descritivo (não muda valor nem estoque): atualiza com histórico."""

    id: str
    forma_pagamento: str
    forma_pagamento_origem: str = "dito"
    motivo: str = ""


@dataclass
class PedidoRelatorio:
    """O atendimento só entende o pedido; quem carrega o livro do período e monta o relatório é o pipeline."""

    periodo: str | None = None
    formato: str = "resumo"  # resumo | itens | por_dia | grafico | planilha


@dataclass
class Anexo:
    """Arquivo que vai junto da resposta (gráfico, planilha)."""

    tipo: str  # image | document
    conteudo: bytes
    mime: str
    nome: str = ""
    legenda: str = ""


@dataclass
class RespostaRelatorio:
    texto: str  # vai por escrito (pode ter tabela)
    fala: str = ""  # o que a voz fala (sem tabela); vazio = o próprio texto
    anexos: list[Anexo] = field(default_factory=list)
    sem_audio: bool = True  # relatório vai só por escrito (economiza a voz e é mais fácil de conferir)


@dataclass
class Plano:
    movimentos: list[Movimento] = field(default_factory=list)
    estoque: dict[str, ProdutoEstoque] = field(default_factory=dict)  # chave -> produto alterado
    lancamentos: list[Lancamento] = field(default_factory=list)
    lancamentos_alterados: list[AlteracaoLancamento] = field(default_factory=list)
    pendencias_novas: list[Pendencia] = field(default_factory=list)
    pendencias_alteradas: list[Pendencia] = field(default_factory=list)  # já existentes (com id)
    pendencias_fechadas: list[FechamentoPendencia] = field(default_factory=list)
    movimentos_alterados: list[AlteracaoMovimento] = field(default_factory=list)
    fiado: list[LancamentoFiado] = field(default_factory=list)
    clientes: dict[str, Cliente] = field(default_factory=dict)  # chave -> cliente depois (novos e alterados)
    configuracoes: dict[str, Any] = field(default_factory=dict)  # preferências do comerciante (gravadas à parte)

    @property
    def vazio(self) -> bool:
        return not any([self.movimentos, self.estoque, self.lancamentos, self.lancamentos_alterados,
                        self.pendencias_novas, self.pendencias_alteradas, self.pendencias_fechadas,
                        self.movimentos_alterados, self.fiado, self.clientes])


@dataclass
class EstadoConversa:
    estoque: dict[str, ProdutoEstoque]
    pendencias: list[Pendencia]  # abertas e dentro da validade, da mais antiga para a mais nova
    movimentos: list[Movimento]  # os últimos N, em ordem cronológica
    lancamentos: list[Lancamento]  # do mês atual (e dos últimos 7 dias), em ordem cronológica
    clientes: dict[str, Cliente] = field(default_factory=dict)  # chave -> cliente do fiado
    fiado: list[LancamentoFiado] = field(default_factory=list)  # os últimos lançamentos do fiado


@dataclass(frozen=True)
class Parametros:
    """Limites e chaves de função. Na F3-01 vêm das configurações e flags do banco."""

    pendencias_ativas: bool = True
    perguntar_valor_compra: bool = True
    confirmacao_ambiguidade: bool = True
    despesas: bool = True
    confianca_minima: float = 0.7
    valor_limite: Decimal = Decimal(1000)
    pendencia_validade_horas: int = 24
    categorias_despesa: tuple[str, ...] = tuple(CATEGORIAS)
    fiado: bool = True
    perguntar_pagamento: bool = False
    pagamento_padrao: str = NAO_INFORMADO
    formas_pagamento: tuple[str, ...] = FORMAS
    # preferências que o próprio comerciante muda pela conversa (para responder "que horas é o fechamento?")
    fechamento_horario: str = ""
    fechamento_mensal: bool = False
    fechamento_formato: str = "lista"
