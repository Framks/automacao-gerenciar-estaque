"""Schema do banco (SQLAlchemy 2). As migrações (Alembic, em migrations/) são a fonte da verdade do DDL.

Convenções: ids bigint identity, tempos timestamptz (UTC), dinheiro numeric(14,2), quantidade numeric(14,3),
custo e valor unitário numeric(14,4). O livro-caixa (movimentos, lancamentos_financeiros) só recebe linhas novas.
"""

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

DINHEIRO = Numeric(14, 2)
QUANTIDADE = Numeric(14, 3)
UNITARIO = Numeric(14, 4)


class Base(DeclarativeBase):
    pass


def _id() -> Mapped[int]:
    return mapped_column(BigInteger, Identity(always=False), primary_key=True)


def _criado_em() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


def _comerciante_fk(nullable: bool = False) -> Mapped[int]:
    return mapped_column(BigInteger, ForeignKey("comerciantes.id", ondelete="CASCADE"), nullable=nullable)


class ComercianteDB(Base):
    __tablename__ = "comerciantes"
    __table_args__ = (CheckConstraint("status IN ('ativo', 'bloqueado', 'pendente')", name="ck_comerciantes_status"),)

    id: Mapped[int] = _id()
    telefone: Mapped[str] = mapped_column(String(20), unique=True, nullable=False)  # wa_id, como a Meta envia
    nome: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    status: Mapped[str] = mapped_column(String(12), nullable=False, server_default="ativo")
    permitido: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    criado_em: Mapped[datetime] = _criado_em()
    atualizado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class ProdutoDB(Base):
    __tablename__ = "produtos"
    __table_args__ = (UniqueConstraint("comerciante_id", "chave", name="uq_produtos_comerciante_chave"),)

    id: Mapped[int] = _id()
    comerciante_id: Mapped[int] = _comerciante_fk()
    nome: Mapped[str] = mapped_column(Text, nullable=False)
    chave: Mapped[str] = mapped_column(Text, nullable=False)  # units.chave_produto(nome)
    unidade: Mapped[str] = mapped_column(String(12), nullable=False)
    quantidade: Mapped[Decimal] = mapped_column(QUANTIDADE, nullable=False, server_default="0")
    custo_medio: Mapped[Decimal | None] = mapped_column(UNITARIO)
    preco_venda: Mapped[Decimal | None] = mapped_column(DINHEIRO)
    atualizado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class MovimentoDB(Base):
    __tablename__ = "movimentos"
    __table_args__ = (
        CheckConstraint("tipo IN ('entrada', 'saida', 'ajuste', 'ajuste_valor')", name="ck_movimentos_tipo"),
        CheckConstraint("forma_pagamento IN ('dinheiro', 'pix', 'cartao', 'fiado', 'nao_informado')",
                        name="ck_movimentos_forma_pagamento"),
        UniqueConstraint("comerciante_id", "origem_id", name="uq_movimentos_origem"),
        Index("ix_movimentos_comerciante_criado", "comerciante_id", "criado_em"),
        Index("ix_movimentos_wa_msg", "comerciante_id", "wa_msg_id"),
    )

    id: Mapped[int] = _id()
    comerciante_id: Mapped[int] = _comerciante_fk()
    produto_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("produtos.id", ondelete="SET NULL"))
    origem_id: Mapped[str] = mapped_column(Text, nullable=False)  # id de domínio: "<wa_msg_id>-<posição>"
    tipo: Mapped[str] = mapped_column(String(14), nullable=False)
    produto: Mapped[str] = mapped_column(Text, nullable=False)  # nome no momento (o produto pode mudar)
    quantidade: Mapped[Decimal] = mapped_column(QUANTIDADE, nullable=False)  # com sinal em ajuste
    unidade: Mapped[str] = mapped_column(String(12), nullable=False)
    valor_total: Mapped[Decimal | None] = mapped_column(DINHEIRO)
    valor_unitario: Mapped[Decimal | None] = mapped_column(UNITARIO)
    texto: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    wa_msg_id: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    mensagem_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("mensagens.id", ondelete="SET NULL"))
    referencia_msg: Mapped[str] = mapped_column(Text, nullable=False, server_default="")  # desfazer: wa_msg_id desfeito
    referencia_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("movimentos.id"))  # ajuste_valor
    autor: Mapped[str] = mapped_column(Text, nullable=False, server_default="")  # "" = bot; admin: e-mail
    motivo: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    forma_pagamento: Mapped[str] = mapped_column(String(14), nullable=False, server_default="nao_informado")
    forma_pagamento_origem: Mapped[str] = mapped_column(String(8), nullable=False, server_default="")
    custo_unitario: Mapped[Decimal | None] = mapped_column(UNITARIO)  # saída: custo médio na hora da venda
    historico_json: Mapped[list] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    criado_em: Mapped[datetime] = _criado_em()


class LancamentoDB(Base):
    __tablename__ = "lancamentos_financeiros"
    __table_args__ = (
        CheckConstraint("tipo IN ('despesa', 'receita', 'retirada', 'ajuste')", name="ck_lancamentos_tipo"),
        UniqueConstraint("comerciante_id", "origem_id", name="uq_lancamentos_origem"),
        Index("ix_lancamentos_comerciante_criado", "comerciante_id", "criado_em"),
    )

    id: Mapped[int] = _id()
    comerciante_id: Mapped[int] = _comerciante_fk()
    origem_id: Mapped[str] = mapped_column(Text, nullable=False)
    tipo: Mapped[str] = mapped_column(String(10), nullable=False)
    categoria: Mapped[str] = mapped_column(Text, nullable=False, server_default="outros")
    descricao: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    valor: Mapped[Decimal] = mapped_column(DINHEIRO, nullable=False)  # com sinal em ajuste
    forma_pagamento: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    texto: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    wa_msg_id: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    referencia_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("lancamentos_financeiros.id"))
    historico_json: Mapped[list] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    autor: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    criado_em: Mapped[datetime] = _criado_em()


class MensagemDB(Base):
    __tablename__ = "mensagens"
    __table_args__ = (
        CheckConstraint("direcao IN ('entrada', 'saida')", name="ck_mensagens_direcao"),
        Index("ix_mensagens_comerciante_criado", "comerciante_id", "criado_em"),
        Index("ix_mensagens_status", "status", "criado_em"),
    )

    id: Mapped[int] = _id()
    comerciante_id: Mapped[int | None] = _comerciante_fk(nullable=True)
    telefone_mascarado: Mapped[str] = mapped_column(String(20), nullable=False, server_default="")  # 5588****1891
    direcao: Mapped[str] = mapped_column(String(8), nullable=False)
    wa_msg_id: Mapped[str | None] = mapped_column(Text, unique=True)
    tipo: Mapped[str] = mapped_column(String(10), nullable=False, server_default="text")  # audio | text | image
    texto: Mapped[str] = mapped_column(Text, nullable=False, server_default="")  # texto recebido ou enviado
    transcricao: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    intencao_json: Mapped[dict | None] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(14), nullable=False, server_default="recebida")
    etapa_erro: Mapped[str] = mapped_column(String(20), nullable=False, server_default="")
    erro: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    tempos_json: Mapped[dict | None] = mapped_column(JSONB)
    tentativas: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    status_entrega: Mapped[str] = mapped_column(String(10), nullable=False, server_default="")
    erro_entrega_json: Mapped[list | None] = mapped_column(JSONB)
    resposta_a: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("mensagens.id", ondelete="SET NULL"))
    criado_em: Mapped[datetime] = _criado_em()
    atualizado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class PendenciaDB(Base):
    __tablename__ = "pendencias"
    __table_args__ = (
        CheckConstraint("tipo IN ('completar', 'confirmacao')", name="ck_pendencias_tipo"),
        CheckConstraint("status IN ('aberta', 'resolvida', 'expirada', 'cancelada')", name="ck_pendencias_status"),
        Index("ix_pendencias_abertas", "comerciante_id", postgresql_where=text("status = 'aberta'")),
    )

    id: Mapped[int] = _id()
    comerciante_id: Mapped[int] = _comerciante_fk()
    tipo: Mapped[str] = mapped_column(String(12), nullable=False)
    rascunho_json: Mapped[dict] = mapped_column(JSONB, nullable=False)  # a operação (v2) como foi entendida
    campos_faltando: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, server_default="{}")
    resumo: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    movimento_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("movimentos.id"))
    lancamento_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("lancamentos_financeiros.id"))
    status: Mapped[str] = mapped_column(String(10), nullable=False, server_default="aberta")
    expira_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    wa_msg_id: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    criado_em: Mapped[datetime] = _criado_em()
    resolvida_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ConfiguracaoDB(Base):
    __tablename__ = "configuracoes"
    __table_args__ = (
        # NULLS NOT DISTINCT: só uma linha global (comerciante_id nulo) por chave
        UniqueConstraint("chave", "comerciante_id", name="uq_configuracoes_chave", postgresql_nulls_not_distinct=True),
    )

    id: Mapped[int] = _id()
    chave: Mapped[str] = mapped_column(Text, nullable=False)
    comerciante_id: Mapped[int | None] = _comerciante_fk(nullable=True)
    valor_json: Mapped[object] = mapped_column(JSONB, nullable=False)
    atualizado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    atualizado_por: Mapped[str] = mapped_column(Text, nullable=False, server_default="")


class FeatureFlagDB(Base):
    __tablename__ = "feature_flags"
    __table_args__ = (
        UniqueConstraint("chave", "comerciante_id", name="uq_feature_flags_chave", postgresql_nulls_not_distinct=True),
    )

    id: Mapped[int] = _id()
    chave: Mapped[str] = mapped_column(Text, nullable=False)
    comerciante_id: Mapped[int | None] = _comerciante_fk(nullable=True)
    ligado: Mapped[bool] = mapped_column(Boolean, nullable=False)
    regra_json: Mapped[dict | None] = mapped_column(JSONB)  # reservado (rollout gradual)
    atualizado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    atualizado_por: Mapped[str] = mapped_column(Text, nullable=False, server_default="")


class AdminDB(Base):
    __tablename__ = "admins"
    __table_args__ = (CheckConstraint("papel IN ('admin', 'leitura')", name="ck_admins_papel"),)

    id: Mapped[int] = _id()
    email: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    senha_hash: Mapped[str] = mapped_column(Text, nullable=False)
    papel: Mapped[str] = mapped_column(String(10), nullable=False, server_default="admin")
    ativo: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    trocar_senha: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    criado_em: Mapped[datetime] = _criado_em()


class AuditoriaDB(Base):
    __tablename__ = "auditoria"
    __table_args__ = (Index("ix_auditoria_criado", "criado_em"),)

    id: Mapped[int] = _id()
    admin_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("admins.id", ondelete="SET NULL"))
    admin_email: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    acao: Mapped[str] = mapped_column(Text, nullable=False)
    entidade: Mapped[str] = mapped_column(Text, nullable=False)
    entidade_id: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    diff_json: Mapped[dict | None] = mapped_column(JSONB)
    criado_em: Mapped[datetime] = _criado_em()


class ClienteDB(Base):
    """Cliente do fiado. `saldo_devedor` é cache da soma de fiado_lancamentos (atualizado na mesma transação)."""

    __tablename__ = "clientes"
    __table_args__ = (UniqueConstraint("comerciante_id", "chave", name="uq_clientes_comerciante_chave"),)

    id: Mapped[int] = _id()
    comerciante_id: Mapped[int] = _comerciante_fk()
    nome: Mapped[str] = mapped_column(Text, nullable=False)
    chave: Mapped[str] = mapped_column(Text, nullable=False)  # clientes.chave_cliente(nome)
    telefone: Mapped[str] = mapped_column(String(20), nullable=False, server_default="")
    saldo_devedor: Mapped[Decimal] = mapped_column(DINHEIRO, nullable=False, server_default="0")
    divida_desde: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    criado_em: Mapped[datetime] = _criado_em()
    atualizado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class FiadoLancamentoDB(Base):
    """Livro do fiado: só recebe linhas novas (dívida +, pagamento −, perdão −, ajuste ±)."""

    __tablename__ = "fiado_lancamentos"
    __table_args__ = (
        CheckConstraint("tipo IN ('divida', 'pagamento', 'ajuste', 'perdao')", name="ck_fiado_tipo"),
        UniqueConstraint("comerciante_id", "origem_id", name="uq_fiado_origem"),
        Index("ix_fiado_comerciante_criado", "comerciante_id", "criado_em"),
        Index("ix_fiado_cliente", "cliente_id"),
    )

    id: Mapped[int] = _id()
    comerciante_id: Mapped[int] = _comerciante_fk()
    cliente_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("clientes.id", ondelete="CASCADE"), nullable=False)
    origem_id: Mapped[str] = mapped_column(Text, nullable=False)
    tipo: Mapped[str] = mapped_column(String(10), nullable=False)
    valor: Mapped[Decimal] = mapped_column(DINHEIRO, nullable=False)
    movimento_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("movimentos.id", ondelete="SET NULL"))
    forma_pagamento: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    texto: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    wa_msg_id: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    referencia_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("fiado_lancamentos.id"))
    autor: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    criado_em: Mapped[datetime] = _criado_em()


class FechamentoDB(Base):
    """Cada fechamento automático enviado (ou tentado). O UNIQUE garante um envio por dia/mês."""

    __tablename__ = "fechamentos"
    __table_args__ = (
        CheckConstraint("tipo IN ('diario', 'mensal')", name="ck_fechamentos_tipo"),
        CheckConstraint("status IN ('enviando', 'enviado', 'sem_janela', 'vazio', 'erro')",
                        name="ck_fechamentos_status"),
        UniqueConstraint("comerciante_id", "tipo", "referencia", name="uq_fechamentos"),
    )

    id: Mapped[int] = _id()
    comerciante_id: Mapped[int] = _comerciante_fk()
    tipo: Mapped[str] = mapped_column(String(8), nullable=False)
    referencia: Mapped[date] = mapped_column(Date, nullable=False)  # o dia (diário) ou o dia 1 do mês (mensal)
    status: Mapped[str] = mapped_column(String(10), nullable=False, server_default="enviando")
    canal: Mapped[str] = mapped_column(String(10), nullable=False, server_default="")  # texto | template
    wa_msg_id: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    erro: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    tentativas: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    avisado: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    criado_em: Mapped[datetime] = _criado_em()
    atualizado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
