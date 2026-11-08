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


# --- F6: meio de pagamento e fiado no banco --------------------------------------------------------------

def _fiado(cliente, **extra):
    return {"forma_pagamento": "fiado", "cliente": cliente, **extra}


def test_fiado_no_banco_saldo_igual_ao_livro(fabrica_pg):
    from sqlalchemy import func

    from app.db.modelos import ClienteDB, FiadoLancamentoDB

    c = ConversaPg(fabrica_pg)
    c.fala("comprei 20 kg de arroz por 100", compra("arroz", 20, valor_total=100))
    assert c.fala("vendi 2 kg de arroz fiado", venda("arroz", 2, valor_total=12) | {"forma_pagamento": "fiado"}) \
        == "Fiado pra quem?"
    assert "Fiado para João, que agora deve 12 reais." in c.fala("pro João", completar(cliente="João"))
    c.fala("vendi 1 kg de arroz no pix por 7", venda("arroz", 1, valor_total=7) | {"forma_pagamento": "pix"})
    c.fala("o João pagou 5 no pix", {"tipo": "recebimento", "cliente": "João", "valor": 5, "forma_pagamento": "pix"})
    c.fala("desfazer", {"tipo": "desfazer"})  # desfaz o pagamento
    c.fala("vendi 1 kg de arroz fiado pra Maria por 6", venda("arroz", 1, valor_total=6) | _fiado("Maria"))

    estado = c.loja.carregar_estado("2026-10-29 11:00:00")
    assert {k: v.saldo for k, v in estado.clientes.items()} == {"joao": Decimal("12.00"), "maria": Decimal("6.00")}
    assert estado.clientes["joao"].divida_desde.startswith("2026-10-29")
    assert [l.tipo for l in estado.fiado] == ["divida", "pagamento", "ajuste", "divida"]
    assert estado.fiado[2].referencia == estado.fiado[1].id  # o desfazer aponta para o pagamento
    assert estado.fiado[0].movimento_ref == "wamid.3-0"
    pix = next(m for m in estado.movimentos if m.forma_pagamento == "pix")
    assert pix.forma_pagamento_origem == "dito" and pix.custo_unitario == Decimal("5.0000")

    with transacao(fabrica_pg) as s:
        for cli in s.scalars(select(ClienteDB)):
            soma = s.scalar(select(func.sum(FiadoLancamentoDB.valor)).where(FiadoLancamentoDB.cliente_id == cli.id))
            assert cli.saldo_devedor == soma


def test_fiado_idempotente_quando_a_fila_repete(fabrica_pg):
    from app.db.modelos import FiadoLancamentoDB

    c = ConversaPg(fabrica_pg)
    c.fala("comprei 20 kg de arroz por 100", compra("arroz", 20, valor_total=100))
    c.fala("vendi fiado pro João", venda("arroz", 2, valor_total=12) | _fiado("João"))
    c.loja.gravar(c.ultimo_plano)  # a fila entregou a mesma mensagem de novo
    estado = c.loja.carregar_estado("2026-10-29 11:00:00")
    assert estado.clientes["joao"].saldo == Decimal("12.00")
    with transacao(fabrica_pg) as s:
        assert len(s.scalars(select(FiadoLancamentoDB)).all()) == 1


def test_mudar_meio_guarda_historico(fabrica_pg):
    c = ConversaPg(fabrica_pg)
    c.fala("comprei 20 kg de arroz por 100", compra("arroz", 20, valor_total=100))
    c.fala("vendi 2 kg de arroz por 12 no pix", venda("arroz", 2, valor_total=12) | {"forma_pagamento": "pix"})
    c.fala("foi no cartão", {"tipo": "corrigir", "movimento_ref": "ultimo", "campos": {"forma_pagamento": "cartao"}})
    with transacao(fabrica_pg) as s:
        m = s.scalar(select(MovimentoDB).where(MovimentoDB.origem_id == "wamid.2-0"))
        assert m.forma_pagamento == "cartao"
        assert m.historico_json[0]["forma_pagamento"] == "pix"
