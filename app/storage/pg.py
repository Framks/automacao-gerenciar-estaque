"""Implementações Postgres dos repositórios de estoque e de comerciantes.

A conversão entre linhas do banco e objetos de domínio fica toda aqui. O domínio continua usando
`data_hora` como texto no fuso do comerciante ("2026-10-29 10:42:00"); o banco guarda timestamptz.
"""

import json
from dataclasses import asdict
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, sessionmaker

from app.db.engine import transacao
from app.db.modelos import ComercianteDB, MovimentoDB, ProdutoDB
from app.domain.models import Comerciante, Movimento, ProdutoEstoque, TipoMovimento
from app.domain.units import chave_produto

FORMATO = "%Y-%m-%d %H:%M:%S"
CACHE_TTL = 3600


def para_datetime(texto: str, tz: ZoneInfo) -> datetime | None:
    if not texto:
        return None
    return datetime.strptime(texto, FORMATO).replace(tzinfo=tz)


def para_texto(momento: datetime | None, tz: ZoneInfo) -> str:
    return momento.astimezone(tz).strftime(FORMATO) if momento else ""


def comerciante_de(linha: ComercianteDB, tz: ZoneInfo) -> Comerciante:
    return Comerciante(linha.telefone, linha.nome, linha.status, para_texto(linha.criado_em, tz), linha.id)


class PgComerciantes:
    """ComercianteRepository no Postgres, com o mesmo cache no Redis do TenantStore."""

    def __init__(self, fabrica: sessionmaker[Session], cache, timezone: str = "America/Fortaleza"):
        self._fabrica = fabrica
        self._cache = cache
        self._tz = ZoneInfo(timezone)

    def obter_ou_criar(self, telefone: str, nome: str | None, agora: str) -> tuple[Comerciante, bool]:
        bruto = self._cache.get(f"pg:tenant:{telefone}")
        if bruto:
            return Comerciante(**json.loads(bruto)), False

        with transacao(self._fabrica) as s:
            linha = s.scalar(select(ComercianteDB).where(ComercianteDB.telefone == telefone))
            novo = linha is None
            if novo:
                # ON CONFLICT: dois workers ao mesmo tempo não quebram (a trava do Redis já evita)
                s.execute(
                    insert(ComercianteDB)
                    .values(telefone=telefone, nome=nome or "", status="ativo", permitido=True,
                            criado_em=para_datetime(agora, self._tz) or datetime.now(self._tz))
                    .on_conflict_do_nothing(index_elements=["telefone"])
                )
                linha = s.scalar(select(ComercianteDB).where(ComercianteDB.telefone == telefone))
            comerciante = comerciante_de(linha, self._tz)

        self._cache.set(f"pg:tenant:{telefone}", json.dumps(asdict(comerciante)), ex=CACHE_TTL)
        return comerciante, novo

    def esquecer_cache(self, telefone: str) -> None:
        self._cache.delete(f"pg:tenant:{telefone}")


def produto_de(linha: ProdutoDB, tz: ZoneInfo) -> ProdutoEstoque:
    return ProdutoEstoque(
        produto=linha.nome, unidade=linha.unidade, quantidade=linha.quantidade, custo_medio=linha.custo_medio,
        atualizado_em=para_texto(linha.atualizado_em, tz), linha=linha.id,
    )


def movimento_de(linha: MovimentoDB, tz: ZoneInfo, origens: dict[int, str]) -> Movimento:
    return Movimento(
        id=linha.origem_id, data_hora=para_texto(linha.criado_em, tz), tipo=TipoMovimento(linha.tipo),
        produto=linha.produto, quantidade=linha.quantidade, unidade=linha.unidade, valor_total=linha.valor_total,
        valor_unitario=linha.valor_unitario, texto_transcrito=linha.texto, whatsapp_msg_id=linha.wa_msg_id,
        referencia=linha.referencia_msg, referencia_mov=origens.get(linha.referencia_id, "") if linha.referencia_id else "",
        forma_pagamento=linha.forma_pagamento, forma_pagamento_origem=linha.forma_pagamento_origem,
        custo_unitario=linha.custo_unitario,
    )


class PgInventoryRepository:
    """InventoryRepository no Postgres. `salvar` grava movimentos e estoque numa transação só."""

    def __init__(self, fabrica: sessionmaker[Session], comerciante_id: int, timezone: str = "America/Fortaleza"):
        self._fabrica = fabrica
        self._cid = comerciante_id
        self._tz = ZoneInfo(timezone)

    def carregar_estoque(self) -> dict[str, ProdutoEstoque]:
        with transacao(self._fabrica) as s:
            linhas = s.scalars(select(ProdutoDB).where(ProdutoDB.comerciante_id == self._cid).order_by(ProdutoDB.id))
            return {linha.chave: produto_de(linha, self._tz) for linha in linhas}

    def carregar_movimentos(self, limite: int | None = None) -> list[Movimento]:
        """Em ordem cronológica. Com `limite`, só os últimos N (o desfazer não precisa do livro todo)."""
        with transacao(self._fabrica) as s:
            consulta = select(MovimentoDB).where(MovimentoDB.comerciante_id == self._cid) \
                .order_by(MovimentoDB.criado_em.desc(), MovimentoDB.id.desc())
            if limite:
                consulta = consulta.limit(limite)
            linhas = list(reversed(s.scalars(consulta).all()))
            refs = {linha.referencia_id for linha in linhas if linha.referencia_id}
            origens = dict(s.execute(select(MovimentoDB.id, MovimentoDB.origem_id).where(MovimentoDB.id.in_(refs)))
                           .all()) if refs else {}
            return [movimento_de(linha, self._tz, origens) for linha in linhas]

    def salvar(self, movimentos: list[Movimento], estoque_alterado: list[ProdutoEstoque]) -> None:
        with transacao(self._fabrica) as s:
            self.salvar_na_sessao(s, movimentos, estoque_alterado)

    def salvar_na_sessao(self, s: Session, movimentos: list[Movimento], estoque_alterado: list[ProdutoEstoque],
                         mensagem_id: int | None = None) -> dict[str, int]:
        """Grava dentro de uma transação já aberta. Devolve {origem_id: id do movimento}.

        Idempotente: se os movimentos desta mensagem já existem (a fila repetiu), não grava de novo.
        """
        if movimentos:
            ja = s.scalar(select(MovimentoDB.id).where(
                MovimentoDB.comerciante_id == self._cid,
                MovimentoDB.origem_id.in_([m.id for m in movimentos]),
            ).limit(1))
            if ja is not None:
                return {}

        chaves = [chave_produto(p.produto) for p in estoque_alterado]
        atuais = {
            linha.chave: linha
            for linha in s.scalars(
                select(ProdutoDB).where(ProdutoDB.comerciante_id == self._cid, ProdutoDB.chave.in_(chaves))
                .with_for_update()  # segunda defesa, além da trava do Redis
            )
        } if chaves else {}

        for p in estoque_alterado:
            chave = chave_produto(p.produto)
            linha = atuais.get(chave)
            momento = para_datetime(p.atualizado_em, self._tz) or datetime.now(self._tz)
            if linha is None:
                linha = ProdutoDB(comerciante_id=self._cid, nome=p.produto, chave=chave, unidade=p.unidade)
                s.add(linha)
                atuais[chave] = linha
            linha.quantidade = p.quantidade
            linha.custo_medio = p.custo_medio
            linha.atualizado_em = momento
        s.flush()

        ids: dict[str, int] = {}
        origens = [m.referencia_mov for m in movimentos if m.referencia_mov]
        refs = dict(s.execute(select(MovimentoDB.origem_id, MovimentoDB.id).where(
            MovimentoDB.comerciante_id == self._cid, MovimentoDB.origem_id.in_(origens))).all()) \
            if origens else {}
        for m in movimentos:
            linha_produto = atuais.get(chave_produto(m.produto))
            linha = MovimentoDB(
                comerciante_id=self._cid,
                produto_id=linha_produto.id if linha_produto is not None else None,
                origem_id=m.id, tipo=m.tipo.value, produto=m.produto, quantidade=m.quantidade, unidade=m.unidade,
                valor_total=m.valor_total, valor_unitario=m.valor_unitario, texto=m.texto_transcrito,
                wa_msg_id=m.whatsapp_msg_id, mensagem_id=mensagem_id, referencia_msg=m.referencia,
                referencia_id=refs.get(m.referencia_mov),
                forma_pagamento=m.forma_pagamento or "nao_informado", forma_pagamento_origem=m.forma_pagamento_origem,
                custo_unitario=m.custo_unitario,
                criado_em=para_datetime(m.data_hora, self._tz) or datetime.now(self._tz),
            )
            s.add(linha)
            s.flush()
            ids[m.id] = linha.id
        return ids
