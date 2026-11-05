"""Migração Sheets -> Postgres com uma planilha falsa: o caso real do feijão, compra sem valor e desfazer."""

from decimal import Decimal

from app.domain import inventory
from app.domain.models import Acao, Intencao, Item
from app.fila.controle import MemoriaRedis
from app.storage.pg import PgInventoryRepository
from app.storage.sheets_repo import SheetsInventoryRepository
from app.storage.tenants import TenantStore
from scripts.migrar_sheets_postgres import conferir, contar, gravar, ler_sheets
from tests.test_storage import FakeSheets


def _planilha():
    """Reproduz o histórico real de 29/10 (feijão) e um desfazer."""
    sheets = FakeSheets()
    TenantStore(sheets, "S", MemoriaRedis()).obter_ou_criar("558881061891", "Francisco", "2026-10-16 10:14:00")
    repo = SheetsInventoryRepository(sheets, "S", "558881061891")
    passos = [
        (Acao.COMPRA, "feijão", 10, None, "a"),
        (Acao.COMPRA, "carne", 5, None, "b"),
        (Acao.VENDA, "feijão", 2, None, "c"),
        (Acao.VENDA, "feijão", 1, 10, "d"),  # o erro real: "o quilo foi 10 reais" virou venda
        (Acao.COMPRA, "arroz", 300, 300, "e"),
        (Acao.COMPRA, "arroz", 10, 50, "f"),
    ]
    for acao, produto, qtd, valor, msg in passos:
        r = inventory.aplicar(Intencao(acao=acao, itens=[Item(produto=produto, quantidade=qtd, unidade="kg",
                                                              valor_total=valor)]),
                              repo.carregar_estoque(), texto=f"fala {msg}", msg_id=msg, agora="2026-10-29 10:00:00")
        repo.salvar(r.movimentos, r.estoque_alterado)
    r = inventory.desfazer(repo.carregar_movimentos(), repo.carregar_estoque(), texto="desfazer", msg_id="g",
                           agora="2026-10-29 10:05:00")
    repo.salvar(r.movimentos, r.estoque_alterado)
    return sheets


def test_planilha_coerente_nao_tem_divergencia():
    (d,) = ler_sheets(_planilha(), "S")
    assert conferir(d) == []
    assert d.estoque["feijao"].quantidade == 7 and d.estoque["arroz"].custo_medio == 1


def test_divergencia_e_detectada():
    sheets = _planilha()
    sheets.abas["558881061891_estoque"][1][2] = 999  # alguém editou a planilha à mão
    (d,) = ler_sheets(sheets, "S")
    assert any("999" in p for p in conferir(d))


def test_grava_e_e_idempotente(fabrica_pg):
    dados = ler_sheets(_planilha(), "S")
    assert gravar(fabrica_pg, dados, "5588981061891", "America/Fortaleza") == {
        "comerciantes": 1, "produtos": 3, "movimentos": 7}
    assert gravar(fabrica_pg, dados, "5588981061891", "America/Fortaleza") == {  # de novo: nada duplica
        "comerciantes": 0, "produtos": 0, "movimentos": 0}
    assert contar(fabrica_pg) == {"comerciantes": 1, "produtos": 3, "movimentos": 7}

    repo = PgInventoryRepository(fabrica_pg, 1)
    estoque = repo.carregar_estoque()
    assert {k: p.quantidade for k, p in estoque.items()} == {"feijao": 7, "carne": 5, "arroz": 300}
    assert estoque["arroz"].custo_medio == Decimal("1")
    movimentos = repo.carregar_movimentos()
    assert movimentos[-1].referencia == "f" and movimentos[0].texto_transcrito == "fala a"
    assert movimentos[0].data_hora == "2026-10-29 10:00:00"


def test_permitido_vem_da_lista(fabrica_pg):
    from sqlalchemy import select

    from app.db.engine import transacao
    from app.db.modelos import ComercianteDB

    gravar(fabrica_pg, ler_sheets(_planilha(), "S"), "", "America/Fortaleza")
    with transacao(fabrica_pg) as s:
        assert s.scalar(select(ComercianteDB.permitido)) is True  # lista vazia = todos
