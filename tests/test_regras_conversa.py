"""Especificação de campos, regras de ambiguidade e montagem do contexto (funções puras)."""

from decimal import Decimal

import pytest

from app.conversa.ambiguidade import avaliar_duvida, tem_verbo_de_operacao
from app.conversa.contexto import LimitesContexto, MensagemHistorico, montar
from app.conversa.modelos import EstadoConversa, Parametros, Pendencia
from app.domain.models import Movimento, ProdutoEstoque, TipoMovimento
from app.domain.operacoes import Operacao, avaliar

ESTOQUE = {"feijao": ProdutoEstoque("feijão", "kg", 8, None, "2026-10-29 10:00:00"),
           "arroz": ProdutoEstoque("arroz", "kg", 300, Decimal(1), "2026-10-29 09:00:00")}


def op(**kw):
    return Operacao.model_validate(kw)


@pytest.mark.parametrize("dados, pronta, faltam_itens, desejaveis", [
    (dict(tipo="compra", itens=[{"produto": "feijão", "quantidade": 10, "unidade": "kg"}]), True,
     [], [["valor"]]),
    (dict(tipo="compra", itens=[{"produto": "feijão"}]), False, [["quantidade"]], [["valor"]]),
    (dict(tipo="compra", itens=[{"produto": "café", "quantidade": 2}]), True, [], [["valor", "unidade"]]),
    (dict(tipo="compra", itens=[{"produto": "arroz", "quantidade": 2, "valor_total": 10}]), True, [], []),
    (dict(tipo="venda", itens=[{"produto": "arroz", "quantidade": 2}]), True, [], [["valor"]]),
])
def test_avaliar_compra_venda(dados, pronta, faltam_itens, desejaveis):
    av = avaliar(op(**dados), ESTOQUE)
    assert av.pronta is pronta
    assert [f.obrigatorios for f in av.itens if f.obrigatorios] == faltam_itens
    assert [f.desejaveis for f in av.itens if f.desejaveis] == desejaveis


def test_avaliar_despesa():
    assert avaliar(op(tipo="despesa", valor="150,59"), {}).desejaveis_faltando == ["categoria"]
    assert avaliar(op(tipo="despesa", valor=10, categoria="aluguel"), {}).desejaveis_faltando == []
    assert not avaliar(op(tipo="despesa"), {}).pronta
    assert avaliar(op(tipo="despesa", valor="150,59"), {}).pronta


def test_verbos():
    assert tem_verbo_de_operacao("Vendi 2 de feijão") and tem_verbo_de_operacao("comprei arroz")
    assert not tem_verbo_de_operacao("o feijão, o quilo foi 10 reais")


def _estado(pendencias=()):
    return EstadoConversa(ESTOQUE, list(pendencias), [], [])


def _descrever(o):
    return "a operação"


def test_duvidas():
    p = Parametros()
    venda_valor = op(tipo="venda", itens=[{"produto": "feijão", "quantidade": 1, "valor_total": 10}])
    assert avaliar_duvida(venda_valor, "o feijão foi 10", _estado(), p, _descrever).motivo == "valor_sem_verbo"
    assert avaliar_duvida(venda_valor, "vendi o feijão por 10", _estado(), p, _descrever) is None
    baixa = op(tipo="compra", itens=[{"produto": "arroz", "quantidade": 1}], confianca=0.5)
    assert avaliar_duvida(baixa, "comprei arroz", _estado(), p, _descrever).motivo == "confianca"
    caro = op(tipo="compra", itens=[{"produto": "arroz", "quantidade": 1, "valor_total": 5}])
    assert avaliar_duvida(caro, "comprei arroz", _estado(), p, _descrever).motivo == "preco_fora"
    venda_cara = op(tipo="venda", itens=[{"produto": "arroz", "quantidade": 1, "valor_total": 5}])
    assert avaliar_duvida(venda_cara, "vendi arroz", _estado(), p, _descrever) is None  # margem alta é normal
    alto = op(tipo="compra", itens=[{"produto": "arroz", "quantidade": 1500, "valor_total": 1500}])
    assert avaliar_duvida(alto, "comprei arroz", _estado(), p, _descrever).motivo == "valor_alto"
    assert avaliar_duvida(alto, "comprei", _estado(), Parametros(confirmacao_ambiguidade=False), _descrever) is None


def test_contexto_limites_e_prioridade():
    estoque = {f"p{i}": ProdutoEstoque(f"p{i}", "un", i, None, f"2026-10-29 09:{i:02d}:00") for i in range(50)}
    estoque["feijao"] = ProdutoEstoque("feijão", "kg", 8, None, "2026-01-01 00:00:00")
    pend = Pendencia("completar", {}, ["valor"], "os 10 quilos de feijão", "Quanto?", "2026-10-30 10:00:00",
                     "2026-10-29 10:00:00", movimento_ref="m1-0", id=12)
    movs = [Movimento(f"m{i}-0", "2026-10-29 10:00:00", TipoMovimento.ENTRADA, "arroz", 1, "kg", None, None, "",
                      f"m{i}") for i in range(10)]
    historico = [MensagemHistorico("comerciante", f"msg {i}", f"2026-10-29 {8 + i // 6:02d}:{i % 60:02d}:00")
                 for i in range(20)]
    ctx = montar("o feijão foi 10 reais", EstadoConversa(estoque, [pend], movs, []), historico,
                 "2026-10-29 10:30:00", LimitesContexto())
    assert ctx.produtos[0]["produto"] == "feijão" and ctx.produtos[0]["custo_medio"] == "sem custo"
    assert len(ctx.produtos) == 40 and len(ctx.ultimos_movimentos) == 5
    assert ctx.pendencias == [{"id": 12, "tipo": "completar", "resumo": "os 10 quilos de feijão",
                               "faltando": ["valor"], "pergunta": "Quanto?"}]
    assert len(ctx.historico) <= 6 and all(h["texto"] for h in ctx.historico)
    assert len(ctx.para_json()) < 9000  # ~3 mil tokens
