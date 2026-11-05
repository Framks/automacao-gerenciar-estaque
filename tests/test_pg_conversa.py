"""Loja e mensagens no Postgres: os diálogos do motor gravando e lendo do banco de verdade."""

from decimal import Decimal

from sqlalchemy import select

from app.conversa.atendimento import Atendimento
from app.db.engine import transacao
from app.db.modelos import LancamentoDB, MensagemDB, MovimentoDB, PendenciaDB
from app.domain.operacoes import RespostaLLM
from app.fila.controle import MemoriaRedis
from app.storage.pg import PgComerciantes
from app.storage.pg_conversa import PgLoja, PgMensagens
from tests.test_pendencias import compra, completar, venda


class ConversaPg:
    def __init__(self, fabrica):
        c, _ = PgComerciantes(fabrica, MemoriaRedis()).obter_ou_criar("558881061891", "Francisco",
                                                                      "2026-10-29 09:00:00")
        self.cid = c.id
        self.loja = PgLoja(fabrica, c.id)
        self.atendimento = Atendimento()
        self.n = 0

    def fala(self, texto, *ops, agora=None):
        self.n += 1
        agora = agora or f"2026-10-29 10:{self.n:02d}:00"
        saida = self.atendimento.processar(RespostaLLM.model_validate({"operacoes": list(ops)}),
                                           self.loja.carregar_estado(agora), texto=texto, msg_id=f"wamid.{self.n}",
                                           agora=agora)
        self.loja.gravar(saida.plano)
        self.ultimo_plano = saida.plano
        return saida.texto


def test_feijao_no_banco(fabrica_pg):
    c = ConversaPg(fabrica_pg)
    c.fala("Comprei 10 kg de feijão.", compra("feijão", 10))
    c.fala("Vendi 2 de feijão", venda("feijão", 2))
    estado = c.loja.carregar_estado("2026-10-29 10:10:00")
    (pd,) = estado.pendencias
    assert pd.movimento_ref == "wamid.1-0" and pd.campos_faltando == ["valor"]
    assert "100 reais no total" in c.fala("o quilo foi 10 reais cada", completar(pd.id, valor_unitario=10))
    estado = c.loja.carregar_estado("2026-10-29 10:20:00")
    assert estado.pendencias == []
    assert estado.estoque["feijao"].custo_medio == Decimal("10.0000") and estado.estoque["feijao"].quantidade == 8
    with transacao(fabrica_pg) as s:
        ajuste = s.scalar(select(MovimentoDB).where(MovimentoDB.tipo == "ajuste_valor"))
        original = s.scalar(select(MovimentoDB).where(MovimentoDB.origem_id == "wamid.1-0"))
        assert ajuste.referencia_id == original.id and ajuste.valor_total == Decimal("100.00")
        p = s.scalar(select(PendenciaDB))
        assert (p.status, p.movimento_id) == ("resolvida", original.id) and p.resolvida_em is not None
    assert estado.movimentos[-1].referencia_mov == "wamid.1-0"


def test_despesa_completada_guarda_historico(fabrica_pg):
    c = ConversaPg(fabrica_pg)
    c.fala("gastei 150,59", {"tipo": "despesa", "valor": "150,59"})
    c.fala("foi a conta de luz", completar(categoria="energia", descricao="conta de luz"))
    with transacao(fabrica_pg) as s:
        l = s.scalar(select(LancamentoDB))
        assert (l.valor, l.categoria, l.descricao) == (Decimal("150.59"), "energia", "conta de luz")
        assert l.historico_json[0]["categoria"] == "outros"
        assert s.scalar(select(PendenciaDB.lancamento_id)) == l.id
    assert "150 reais e 59 centavos" in c.fala("quanto gastei?", {"tipo": "consulta", "assunto": "gastos"},
                                               agora="2026-10-29 11:00:00")
    assert c.fala("desfazer", {"tipo": "desfazer"}, agora="2026-10-29 11:01:00") == \
        "Pronto, desfiz o gasto de 150 reais e 59 centavos."
    assert "não anotou" in c.fala("quanto gastei?", {"tipo": "consulta", "assunto": "gastos"},
                                  agora="2026-10-29 11:02:00")


def test_gravar_de_novo_nao_duplica(fabrica_pg):
    c = ConversaPg(fabrica_pg)
    c.fala("Comprei 10 kg de feijão.", compra("feijão", 10))
    c.loja.gravar(c.ultimo_plano)  # a fila repetiu
    with transacao(fabrica_pg) as s:
        assert len(s.scalars(select(MovimentoDB)).all()) == 1
        assert len(s.scalars(select(PendenciaDB)).all()) == 1
    assert c.loja.carregar_estado("2026-10-29 10:05:00").estoque["feijao"].quantidade == 10


def test_pendencia_vencida_nao_volta(fabrica_pg):
    c = ConversaPg(fabrica_pg)
    c.fala("comprei carne", compra("carne", 5), agora="2026-10-29 10:00:00")
    assert c.loja.carregar_estado("2026-10-30 09:00:00").pendencias
    assert c.loja.carregar_estado("2026-10-30 10:00:01").pendencias == []
    assert PgMensagens(fabrica_pg).expirar_pendencias() == 1


def test_mensagens_entrada_saida_entrega_e_historico(fabrica_pg):
    c = ConversaPg(fabrica_pg)
    m = PgMensagens(fabrica_pg)
    mid, status = m.registrar_entrada(c.cid, "wamid.E1", "audio")
    assert status == "recebida"
    assert m.registrar_entrada(c.cid, "wamid.E1", "audio")[0] == mid  # fila repetiu: mesma linha
    m.atualizar(mid, status="processada", transcricao="comprei 10 kg de feijão", tempos_json={"transcricao": 1.2})
    m.registrar_saida(c.cid, mid, "text", "Anotado! Quanto você pagou?", "wamid.S1")
    m.registrar_saida(c.cid, mid, "audio", "Anotado! Quanto você pagou?", "wamid.S2")
    assert m.atualizar_entrega("wamid.S1", "read")
    assert m.atualizar_entrega("wamid.S1", "delivered")  # fora de ordem: continua read
    assert not m.atualizar_entrega("wamid.outro", "read")
    with transacao(fabrica_pg) as s:
        e = s.scalar(select(MensagemDB).where(MensagemDB.wa_msg_id == "wamid.E1"))
        assert e.tentativas == 2
        assert s.scalar(select(MensagemDB.status_entrega).where(MensagemDB.wa_msg_id == "wamid.S1")) == "read"
    historico = m.historico(c.cid, "2000-01-01 00:00:00")
    assert [(h.de, h.texto) for h in historico] == [("comerciante", "comprei 10 kg de feijão"),
                                                    ("bot", "Anotado! Quanto você pagou?")]


def test_falha_de_entrega_marca_erro(fabrica_pg):
    m = PgMensagens(fabrica_pg)
    m.registrar_saida(None, None, "audio", "oi", "wamid.F")
    m.atualizar_entrega("wamid.F", "failed", [{"code": 130497}])
    with transacao(fabrica_pg) as s:
        linha = s.scalar(select(MensagemDB))
        assert (linha.status, linha.erro_entrega_json) == ("erro", [{"code": 130497}])
