"""Contrato dos repositórios: o mesmo cenário tem que funcionar no Sheets (falso) e no Postgres."""

import threading
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.db.engine import transacao
from app.db.modelos import MovimentoDB, ProdutoDB
from app.domain import inventory
from app.domain.models import Acao, Intencao, Item
from app.fila.controle import MemoriaRedis
from app.storage.pg import PgComerciantes, PgInventoryRepository
from app.storage.sheets_repo import SheetsInventoryRepository
from app.storage.tenants import TenantStore
from tests.test_storage import FakeSheets

AGORA = "2026-10-29 10:00:00"


@pytest.fixture(params=["sheets", "postgres"])
def armazenamento(request):
    """(comerciantes, fabrica_de_repositorio) de cada implementação."""
    if request.param == "sheets":
        sheets = FakeSheets()
        return TenantStore(sheets, "S", MemoriaRedis()), lambda c: SheetsInventoryRepository(sheets, "S", c.telefone)
    fabrica = request.getfixturevalue("fabrica_pg")
    return PgComerciantes(fabrica, MemoriaRedis()), lambda c: PgInventoryRepository(fabrica, c.id)


def _aplicar(repo, acao, qtd, msg_id, produto="arroz", unidade="kg", valor=None):
    i = Intencao(acao=acao, itens=[Item(produto=produto, quantidade=qtd, unidade=unidade, valor_total=valor)])
    r = inventory.aplicar(i, repo.carregar_estoque(), texto="fala", msg_id=msg_id, agora=AGORA)
    assert r.ok, r.problemas
    repo.salvar(r.movimentos, r.estoque_alterado)


def test_cadastro_ida_e_volta(armazenamento):
    comerciantes, _ = armazenamento
    c, novo = comerciantes.obter_ou_criar("5585999990001", "Zé", AGORA)
    assert novo and (c.telefone, c.nome, c.status) == ("5585999990001", "Zé", "ativo")
    c2, novo2 = comerciantes.obter_ou_criar("5585999990001", "Outro nome", AGORA)
    assert not novo2 and c2.nome == "Zé"


def test_estoque_e_movimentos_ida_e_volta(armazenamento):
    comerciantes, repo_de = armazenamento
    repo = repo_de(comerciantes.obter_ou_criar("5585", "Zé", AGORA)[0])

    _aplicar(repo, Acao.COMPRA, 300, "m1", valor=300)
    _aplicar(repo, Acao.COMPRA, 5, "m2", produto="feijão")
    _aplicar(repo, Acao.VENDA, Decimal("10.5"), "m3")

    estoque = repo.carregar_estoque()
    assert {k: p.quantidade for k, p in estoque.items()} == {"arroz": Decimal("289.5"), "feijao": 5}
    assert estoque["arroz"].custo_medio == 1
    assert estoque["feijao"].custo_medio is None
    movimentos = repo.carregar_movimentos()
    assert [(m.whatsapp_msg_id, m.tipo.value, m.quantidade) for m in movimentos] == [
        ("m1", "entrada", 300), ("m2", "entrada", 5), ("m3", "saida", Decimal("10.5"))]
    assert movimentos[0].valor_total == 300 and movimentos[0].data_hora == AGORA


def test_desfazer_ida_e_volta(armazenamento):
    comerciantes, repo_de = armazenamento
    repo = repo_de(comerciantes.obter_ou_criar("5585", "Zé", AGORA)[0])
    _aplicar(repo, Acao.COMPRA, 10, "m1", valor=50)
    r = inventory.desfazer(repo.carregar_movimentos(), repo.carregar_estoque(), texto="desfaz", msg_id="d1",
                           agora=AGORA)
    repo.salvar(r.movimentos, r.estoque_alterado)
    assert repo.carregar_estoque()["arroz"].quantidade == 0
    assert repo.carregar_movimentos()[-1].referencia == "m1"


def test_comerciantes_isolados(armazenamento):
    comerciantes, repo_de = armazenamento
    a = repo_de(comerciantes.obter_ou_criar("111", "", AGORA)[0])
    b = repo_de(comerciantes.obter_ou_criar("222", "", AGORA)[0])
    _aplicar(a, Acao.COMPRA, 1, "m1")
    assert b.carregar_estoque() == {} and b.carregar_movimentos() == []
    assert len(a.carregar_estoque()) == 1


# --- só Postgres ---

def _repo_pg(fabrica_pg, telefone="5585"):
    c, _ = PgComerciantes(fabrica_pg, MemoriaRedis()).obter_ou_criar(telefone, "Zé", AGORA)
    return PgInventoryRepository(fabrica_pg, c.id)


def test_salvar_e_atomico(fabrica_pg, monkeypatch):
    repo = _repo_pg(fabrica_pg)
    i = Intencao(acao=Acao.COMPRA, itens=[Item(produto="arroz", quantidade=1)])
    r = inventory.aplicar(i, {}, texto="", msg_id="m1", agora=AGORA)

    original = MovimentoDB.__init__

    def quebrar(self, **kw):
        raise RuntimeError("falha no meio")

    monkeypatch.setattr(MovimentoDB, "__init__", quebrar)
    with pytest.raises(RuntimeError):
        repo.salvar(r.movimentos, r.estoque_alterado)  # o produto já tinha sido inserido (flush) antes
    monkeypatch.setattr(MovimentoDB, "__init__", original)

    with transacao(fabrica_pg) as s:
        assert s.scalar(select(func.count()).select_from(ProdutoDB)) == 0
        assert s.scalar(select(func.count()).select_from(MovimentoDB)) == 0


def test_salvar_de_novo_nao_duplica(fabrica_pg):
    repo = _repo_pg(fabrica_pg)
    i = Intencao(acao=Acao.COMPRA, itens=[Item(produto="arroz", quantidade=1)])
    r = inventory.aplicar(i, {}, texto="", msg_id="m1", agora=AGORA)
    repo.salvar(r.movimentos, r.estoque_alterado)
    repo.salvar(r.movimentos, r.estoque_alterado)  # a fila repetiu
    assert len(repo.carregar_movimentos()) == 1


def test_carregar_movimentos_com_limite(fabrica_pg):
    repo = _repo_pg(fabrica_pg)
    for k in range(5):
        _aplicar(repo, Acao.COMPRA, 1, f"m{k}")
    assert [m.whatsapp_msg_id for m in repo.carregar_movimentos(limite=2)] == ["m3", "m4"]


def test_duas_sessoes_no_mesmo_produto_esperam_a_trava_da_linha(fabrica_pg):
    """FOR UPDATE: a segunda transação espera a primeira terminar (defesa além da trava do Redis)."""
    repo = _repo_pg(fabrica_pg)
    _aplicar(repo, Acao.COMPRA, 10, "m0")
    ordem = []
    liberar = threading.Event()

    def primeira():
        with transacao(fabrica_pg) as s:
            s.execute(select(ProdutoDB).with_for_update())
            ordem.append("primeira travou")
            liberar.wait(5)
            ordem.append("primeira terminou")

    t = threading.Thread(target=primeira)
    t.start()
    while not ordem:
        pass
    segunda = threading.Thread(target=lambda: (_aplicar(repo, Acao.VENDA, 1, "m1"), ordem.append("segunda gravou")))
    segunda.start()
    segunda.join(0.5)
    assert ordem == ["primeira travou"]  # a segunda está esperando
    liberar.set()
    t.join()
    segunda.join(5)
    assert ordem == ["primeira travou", "primeira terminou", "segunda gravou"]
    assert repo.carregar_estoque()["arroz"].quantidade == 9
