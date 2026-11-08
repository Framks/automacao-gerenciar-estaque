"""Postgres da conversa: a loja (estado + gravação do plano) e o registro de mensagens."""

from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, sessionmaker

from app.conversa.contexto import MensagemHistorico
from app.conversa.modelos import EstadoConversa, Pendencia, Plano
from app.db.engine import transacao
from app.db.modelos import (
    ClienteDB,
    FiadoLancamentoDB,
    LancamentoDB,
    MensagemDB,
    MovimentoDB,
    PendenciaDB,
    ProdutoDB,
)
from app.domain.fiado import Cliente, LancamentoFiado
from app.domain.financeiro import Lancamento
from app.storage.pg import PgInventoryRepository, movimento_de, para_datetime, para_texto, produto_de

LIMITE_MOVIMENTOS = 50
LIMITE_FIADO = 200


class PgLoja:
    """Loja (app/conversa/loja.py) de um comerciante."""

    def __init__(self, fabrica: sessionmaker[Session], comerciante_id: int, timezone: str = "America/Fortaleza"):
        self._fabrica = fabrica
        self._cid = comerciante_id
        self._tz = ZoneInfo(timezone)
        self._estoque = PgInventoryRepository(fabrica, comerciante_id, timezone)

    def carregar_estado(self, agora: str) -> EstadoConversa:
        momento = para_datetime(agora, self._tz)
        desde_lanc = min(momento.replace(day=1, hour=0, minute=0, second=0), momento - timedelta(days=7))
        with transacao(self._fabrica) as s:
            estoque = {p.chave: produto_de(p, self._tz)
                       for p in s.scalars(select(ProdutoDB).where(ProdutoDB.comerciante_id == self._cid))}
            pendencias = [self._pendencia_de(p) for p in s.scalars(
                select(PendenciaDB).where(PendenciaDB.comerciante_id == self._cid, PendenciaDB.status == "aberta",
                                          PendenciaDB.expira_em > momento).order_by(PendenciaDB.id))]
            lancamentos = [self._lancamento_de(l, s) for l in s.scalars(
                select(LancamentoDB).where(LancamentoDB.comerciante_id == self._cid,
                                           LancamentoDB.criado_em >= desde_lanc).order_by(LancamentoDB.id))]
            clientes = {c.chave: self._cliente_de(c) for c in s.scalars(
                select(ClienteDB).where(ClienteDB.comerciante_id == self._cid))}
            fiado = self._carregar_fiado(s, limite=LIMITE_FIADO)
        movimentos = self._estoque.carregar_movimentos(limite=LIMITE_MOVIMENTOS)
        return EstadoConversa(estoque, pendencias, movimentos, lancamentos, clientes, fiado)

    def carregar_livro(self, desde: datetime) -> tuple[list, list[Lancamento], list[LancamentoFiado]]:
        """Movimentos, lançamentos e fiado desde um momento (até agora), em ordem cronológica (relatórios)."""
        with transacao(self._fabrica) as s:
            linhas = s.scalars(select(MovimentoDB).where(MovimentoDB.comerciante_id == self._cid,
                                                         MovimentoDB.criado_em >= desde)
                               .order_by(MovimentoDB.criado_em, MovimentoDB.id)).all()
            refs = {l.referencia_id for l in linhas if l.referencia_id}
            origens = dict(s.execute(select(MovimentoDB.id, MovimentoDB.origem_id).where(MovimentoDB.id.in_(refs)))
                           .all()) if refs else {}
            movimentos = [movimento_de(l, self._tz, origens) for l in linhas]
            lancamentos = [self._lancamento_de(l, s) for l in s.scalars(
                select(LancamentoDB).where(LancamentoDB.comerciante_id == self._cid, LancamentoDB.criado_em >= desde)
                .order_by(LancamentoDB.criado_em, LancamentoDB.id))]
            fiado = self._carregar_fiado(s, limite=None, desde=desde)
        return movimentos, lancamentos, fiado

    def gravar(self, plano: Plano, mensagem_id: int | None = None) -> None:
        if plano.vazio:
            return
        with transacao(self._fabrica) as s:
            if self._ja_gravado(s, plano):
                return  # a fila repetiu uma mensagem que já tinha sido gravada
            ids_mov = self._estoque.salvar_na_sessao(s, plano.movimentos, list(plano.estoque.values()), mensagem_id) \
                if plano.movimentos or plano.estoque else {}

            for alt in plano.movimentos_alterados:
                linha = s.scalar(select(MovimentoDB).where(MovimentoDB.comerciante_id == self._cid,
                                                           MovimentoDB.origem_id == alt.id))
                if linha is None:
                    continue
                historico = list(linha.historico_json or []) + [{
                    "em": datetime.now(self._tz).isoformat(timespec="seconds"),
                    "forma_pagamento": linha.forma_pagamento, "motivo": alt.motivo,
                }]
                linha.forma_pagamento, linha.forma_pagamento_origem = alt.forma_pagamento, alt.forma_pagamento_origem
                linha.historico_json = historico

            if plano.fiado:
                self._gravar_fiado(s, plano, ids_mov)

            ids_lanc: dict[str, int] = {}
            for l in plano.lancamentos:
                ref = self._lancamento_id(s, l.referencia) if l.referencia else None
                linha = LancamentoDB(
                    comerciante_id=self._cid, origem_id=l.id, tipo=l.tipo, categoria=l.categoria,
                    descricao=l.descricao, valor=l.valor, texto=l.texto, wa_msg_id=l.wa_msg_id, referencia_id=ref,
                    forma_pagamento=l.forma_pagamento,
                    criado_em=para_datetime(l.data_hora, self._tz),
                )
                s.add(linha)
                s.flush()
                ids_lanc[l.id] = linha.id

            for alt in plano.lancamentos_alterados:
                linha = s.scalar(select(LancamentoDB).where(LancamentoDB.comerciante_id == self._cid,
                                                            LancamentoDB.origem_id == alt.id))
                if linha is None:
                    continue
                historico = list(linha.historico_json or []) + [{
                    "em": datetime.now(self._tz).isoformat(timespec="seconds"),
                    "categoria": linha.categoria, "descricao": linha.descricao, "motivo": alt.motivo,
                }]
                linha.categoria, linha.descricao, linha.historico_json = alt.categoria, alt.descricao, historico

            agora = datetime.now(self._tz)
            for f in plano.pendencias_fechadas:
                s.execute(update(PendenciaDB).where(PendenciaDB.id == f.id, PendenciaDB.comerciante_id == self._cid)
                          .values(status=f.status, resolvida_em=agora))

            for p in plano.pendencias_novas:
                s.add(PendenciaDB(
                    comerciante_id=self._cid, tipo=p.tipo, campos_faltando=p.campos_faltando, resumo=p.resumo,
                    rascunho_json={"operacao": p.operacao, "movimento_ref": p.movimento_ref,
                                   "lancamento_ref": p.lancamento_ref, "item": p.item, "pergunta": p.pergunta,
                                   "chave_local": p.chave_local},
                    movimento_id=ids_mov.get(p.movimento_ref) or self._movimento_id(s, p.movimento_ref),
                    lancamento_id=ids_lanc.get(p.lancamento_ref) or self._lancamento_id(s, p.lancamento_ref),
                    expira_em=para_datetime(p.expira_em, self._tz), wa_msg_id=p.wa_msg_id,
                    criado_em=para_datetime(p.criado_em, self._tz),
                ))

    # --- apoio -----------------------------------------------------------------------------------------

    def _gravar_fiado(self, s: Session, plano: Plano, ids_mov: dict[str, int]) -> None:
        """Clientes novos, lançamentos do fiado e o saldo (somado no banco, sob FOR UPDATE)."""
        chaves = sorted({l.cliente for l in plano.fiado})
        linhas = {c.chave: c for c in s.scalars(
            select(ClienteDB).where(ClienteDB.comerciante_id == self._cid, ClienteDB.chave.in_(chaves))
            .with_for_update())}
        for chave in chaves:
            if chave not in linhas:
                cliente = plano.clientes.get(chave)
                nome = cliente.nome if cliente else next(l.cliente_nome for l in plano.fiado if l.cliente == chave)
                s.execute(insert(ClienteDB).values(comerciante_id=self._cid, nome=nome, chave=chave)
                          .on_conflict_do_nothing(constraint="uq_clientes_comerciante_chave"))
                linhas[chave] = s.scalar(select(ClienteDB).where(ClienteDB.comerciante_id == self._cid,
                                                                 ClienteDB.chave == chave).with_for_update())
        ids: dict[str, int] = {}
        for l in plano.fiado:
            cliente = linhas[l.cliente]
            momento = para_datetime(l.data_hora, self._tz)
            linha = FiadoLancamentoDB(
                comerciante_id=self._cid, cliente_id=cliente.id, origem_id=l.id, tipo=l.tipo, valor=l.valor,
                movimento_id=ids_mov.get(l.movimento_ref) or self._movimento_id(s, l.movimento_ref),
                forma_pagamento=l.forma_pagamento, texto=l.texto, wa_msg_id=l.wa_msg_id,
                referencia_id=ids.get(l.referencia) or self._fiado_id(s, l.referencia), criado_em=momento,
            )
            s.add(linha)
            s.flush()
            ids[l.id] = linha.id
            antes = Decimal(cliente.saldo_devedor)
            cliente.saldo_devedor = antes + l.valor
            if antes <= 0 < cliente.saldo_devedor:
                cliente.divida_desde = momento
            if cliente.saldo_devedor <= 0:
                cliente.divida_desde = None
            cliente.atualizado_em = momento
        s.flush()

    def _carregar_fiado(self, s: Session, limite: int | None, desde: datetime | None = None
                        ) -> list[LancamentoFiado]:
        consulta = select(FiadoLancamentoDB, ClienteDB.chave, ClienteDB.nome).join(ClienteDB) \
            .where(FiadoLancamentoDB.comerciante_id == self._cid)
        if desde is not None:
            consulta = consulta.where(FiadoLancamentoDB.criado_em >= desde)
        consulta = consulta.order_by(FiadoLancamentoDB.criado_em.desc(), FiadoLancamentoDB.id.desc())
        if limite:
            consulta = consulta.limit(limite)
        linhas = list(reversed(s.execute(consulta).all()))
        refs = {l.referencia_id for l, _, _ in linhas if l.referencia_id} | {l.movimento_id for l, _, _ in linhas
                                                                            if l.movimento_id}
        origens_f = dict(s.execute(select(FiadoLancamentoDB.id, FiadoLancamentoDB.origem_id)
                                   .where(FiadoLancamentoDB.id.in_(refs))).all()) if refs else {}
        origens_m = dict(s.execute(select(MovimentoDB.id, MovimentoDB.origem_id)
                                   .where(MovimentoDB.id.in_(refs))).all()) if refs else {}
        return [LancamentoFiado(
            id=l.origem_id, data_hora=para_texto(l.criado_em, self._tz), cliente=chave, cliente_nome=nome,
            tipo=l.tipo, valor=Decimal(l.valor), movimento_ref=origens_m.get(l.movimento_id, ""),
            forma_pagamento=l.forma_pagamento, texto=l.texto, wa_msg_id=l.wa_msg_id,
            referencia=origens_f.get(l.referencia_id, "") if l.referencia_id else "",
        ) for l, chave, nome in linhas]

    def _cliente_de(self, c: ClienteDB) -> Cliente:
        return Cliente(nome=c.nome, chave=c.chave, saldo=Decimal(c.saldo_devedor),
                       divida_desde=para_texto(c.divida_desde, self._tz), id=c.id,
                       atualizado_em=para_texto(c.atualizado_em, self._tz))

    def _fiado_id(self, s: Session, origem: str) -> int | None:
        if not origem:
            return None
        return s.scalar(select(FiadoLancamentoDB.id).where(FiadoLancamentoDB.comerciante_id == self._cid,
                                                           FiadoLancamentoDB.origem_id == origem))

    def _ja_gravado(self, s: Session, plano: Plano) -> bool:
        origens_fiado = [l.id for l in plano.fiado]
        if origens_fiado and s.scalar(select(FiadoLancamentoDB.id).where(
                FiadoLancamentoDB.comerciante_id == self._cid, FiadoLancamentoDB.origem_id.in_(origens_fiado))
                .limit(1)):
            return True
        origens_mov = [m.id for m in plano.movimentos]
        origens_lanc = [l.id for l in plano.lancamentos]
        chaves = [p.chave_local for p in plano.pendencias_novas]
        if origens_mov and s.scalar(select(MovimentoDB.id).where(
                MovimentoDB.comerciante_id == self._cid, MovimentoDB.origem_id.in_(origens_mov)).limit(1)):
            return True
        if origens_lanc and s.scalar(select(LancamentoDB.id).where(
                LancamentoDB.comerciante_id == self._cid, LancamentoDB.origem_id.in_(origens_lanc)).limit(1)):
            return True
        if chaves and s.scalar(select(PendenciaDB.id).where(
                PendenciaDB.comerciante_id == self._cid,
                PendenciaDB.rascunho_json["chave_local"].astext.in_(chaves)).limit(1)):
            return True
        return False

    def _movimento_id(self, s: Session, origem: str) -> int | None:
        if not origem:
            return None
        return s.scalar(select(MovimentoDB.id).where(MovimentoDB.comerciante_id == self._cid,
                                                     MovimentoDB.origem_id == origem))

    def _lancamento_id(self, s: Session, origem: str) -> int | None:
        if not origem:
            return None
        return s.scalar(select(LancamentoDB.id).where(LancamentoDB.comerciante_id == self._cid,
                                                      LancamentoDB.origem_id == origem))

    def _pendencia_de(self, p: PendenciaDB) -> Pendencia:
        r = p.rascunho_json or {}
        return Pendencia(
            tipo=p.tipo, operacao=r.get("operacao", {}), campos_faltando=list(p.campos_faltando or []),
            resumo=p.resumo, pergunta=r.get("pergunta", ""), expira_em=para_texto(p.expira_em, self._tz),
            criado_em=para_texto(p.criado_em, self._tz), movimento_ref=r.get("movimento_ref", ""),
            lancamento_ref=r.get("lancamento_ref", ""), item=r.get("item", 0), status=p.status,
            wa_msg_id=p.wa_msg_id, id=p.id, chave_local=r.get("chave_local", ""),
        )

    def _lancamento_de(self, l: LancamentoDB, s: Session) -> Lancamento:
        referencia = ""
        if l.referencia_id:
            referencia = s.scalar(select(LancamentoDB.origem_id).where(LancamentoDB.id == l.referencia_id)) or ""
        return Lancamento(id=l.origem_id, data_hora=para_texto(l.criado_em, self._tz), tipo=l.tipo,
                          valor=Decimal(l.valor), categoria=l.categoria, descricao=l.descricao, texto=l.texto,
                          wa_msg_id=l.wa_msg_id, referencia=referencia, forma_pagamento=l.forma_pagamento)


class PgMensagens:
    """Registro de cada mensagem recebida e enviada: status, etapa do erro, tempos e entrega."""

    def __init__(self, fabrica: sessionmaker[Session], timezone: str = "America/Fortaleza"):
        self._fabrica = fabrica
        self._tz = ZoneInfo(timezone)

    def registrar_entrada(self, comerciante_id: int | None, wa_msg_id: str, tipo: str, texto: str = "",
                          telefone_mascarado: str = "") -> tuple[int, str]:
        """Cria (ou, se a fila repetiu, conta mais uma tentativa). Devolve (id, status atual)."""
        with transacao(self._fabrica) as s:
            linha = s.execute(
                insert(MensagemDB).values(
                    comerciante_id=comerciante_id, direcao="entrada", wa_msg_id=wa_msg_id, tipo=tipo, texto=texto,
                    status="recebida", tentativas=1, telefone_mascarado=telefone_mascarado,
                ).on_conflict_do_update(
                    index_elements=["wa_msg_id"],
                    set_={"tentativas": MensagemDB.tentativas + 1, "atualizado_em": func.now()},
                ).returning(MensagemDB.id, MensagemDB.status)
            ).one()
            return linha[0], linha[1]

    def atualizar(self, mensagem_id: int, **campos) -> None:
        with transacao(self._fabrica) as s:
            s.execute(update(MensagemDB).where(MensagemDB.id == mensagem_id).values(**campos, atualizado_em=func.now()))

    def registrar_saida(self, comerciante_id: int | None, resposta_a: int | None, tipo: str, texto: str,
                        wa_msg_id: str | None, erro: str = "") -> None:
        with transacao(self._fabrica) as s:
            s.add(MensagemDB(
                comerciante_id=comerciante_id, direcao="saida", wa_msg_id=wa_msg_id or None, tipo=tipo, texto=texto,
                status="erro" if erro else "enviada", erro=erro, resposta_a=resposta_a,
                status_entrega="" if erro else "accepted",
            ))

    def atualizar_entrega(self, wa_msg_id: str, status: str, erros: list | None = None) -> bool:
        """Status vindo do webhook da Meta (sent, delivered, read, failed). True se achou a mensagem."""
        ordem = {"accepted": 0, "sent": 1, "delivered": 2, "read": 3, "failed": 4}
        with transacao(self._fabrica) as s:
            linha = s.scalar(select(MensagemDB).where(MensagemDB.wa_msg_id == wa_msg_id,
                                                      MensagemDB.direcao == "saida"))
            if linha is None:
                return False
            if ordem.get(status, 0) >= ordem.get(linha.status_entrega, 0):  # "read" não volta para "delivered"
                linha.status_entrega = status
            if erros:
                linha.erro_entrega_json = erros
                linha.status = "erro"
            linha.atualizado_em = datetime.now(self._tz)
            return True

    def historico(self, comerciante_id: int, desde: str, limite: int = 6) -> list[MensagemHistorico]:
        inicio = para_datetime(desde, self._tz)
        with transacao(self._fabrica) as s:
            linhas = s.scalars(
                select(MensagemDB).where(
                    MensagemDB.comerciante_id == comerciante_id, MensagemDB.criado_em >= inicio,
                    or_(MensagemDB.direcao == "entrada", MensagemDB.tipo == "text"),
                    MensagemDB.status.in_(("processada", "enviada")),
                ).order_by(MensagemDB.criado_em.desc(), MensagemDB.id.desc()).limit(limite)
            ).all()
            return [MensagemHistorico("comerciante" if m.direcao == "entrada" else "bot",
                                      m.transcricao or m.texto, para_texto(m.criado_em, self._tz))
                    for m in reversed(linhas)]

    def limpar(self, dias: int) -> int:
        """Retenção: apaga mensagens mais antigas que `dias` (o livro-caixa não é tocado)."""
        with transacao(self._fabrica) as s:
            limite = datetime.now(self._tz) - timedelta(days=dias)
            s.execute(update(MovimentoDB).where(MovimentoDB.mensagem_id.in_(
                select(MensagemDB.id).where(MensagemDB.criado_em < limite))).values(mensagem_id=None))
            return s.execute(delete(MensagemDB).where(MensagemDB.criado_em < limite)).rowcount or 0

    def expirar_pendencias(self) -> int:
        with transacao(self._fabrica) as s:
            return s.execute(update(PendenciaDB).where(PendenciaDB.status == "aberta",
                                                       PendenciaDB.expira_em <= func.now())
                             .values(status="expirada", resolvida_em=func.now())).rowcount or 0
