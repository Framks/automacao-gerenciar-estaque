"""Camada de armazenamento contra uma planilha simulada em memória (sem rede)."""

import re

from app.domain import inventory
from app.domain.models import Acao, Intencao, Item
from app.fila.controle import MemoriaRedis
from app.storage.sheets_repo import SheetsInventoryRepository, garantir_abas
from app.storage.tenants import TenantStore


class _Req:
    def __init__(self, fn):
        self._fn = fn

    def execute(self):
        return self._fn()


def _faixa(texto):  # "'aba'!A2:E" -> ("aba", 2)
    m = re.match(r"'(.+)'!A(\d+)", texto)
    return m.group(1), int(m.group(2))


class FakeValues:
    def __init__(self, fake):
        self.f = fake

    def get(self, spreadsheetId, range, valueRenderOption=None):
        aba, inicio = _faixa(range)
        return _Req(lambda: {"values": self.f.abas[aba][inicio - 1:]} if self.f.abas[aba][inicio - 1:] else {})

    def append(self, spreadsheetId, range, valueInputOption, insertDataOption, body):
        aba, _ = _faixa(range)
        return _Req(lambda: self.f.abas[aba].extend(body["values"]) or {})

    def batchUpdate(self, spreadsheetId, body):
        def aplicar():
            for d in body["data"]:
                aba, inicio = _faixa(d["range"])
                linhas = self.f.abas[aba]
                for k, valores in enumerate(d["values"]):
                    while len(linhas) < inicio + k:
                        linhas.append([])
                    linhas[inicio - 1 + k] = list(valores)
            return {}
        return _Req(aplicar)


class FakeSheets:
    """Imita sheets.spreadsheets() e .values() do googleapiclient, guardando as abas num dict."""

    def __init__(self):
        self.abas: dict[str, list[list]] = {}
        self.abas_criadas = 0

    def spreadsheets(self):
        return self

    def values(self):
        return FakeValues(self)

    def get(self, spreadsheetId, fields=None):
        return _Req(lambda: {"sheets": [{"properties": {"title": t}} for t in self.abas]})

    def batchUpdate(self, spreadsheetId, body):
        def aplicar():
            for r in body["requests"]:
                titulo = r["addSheet"]["properties"]["title"]
                assert titulo not in self.abas, f"aba duplicada: {titulo}"
                self.abas[titulo] = []
                self.abas_criadas += 1
            return {}
        return _Req(aplicar)


def test_primeiro_contato_cria_usuarios_e_abas_do_comerciante():
    sheets = FakeSheets()
    c, novo = TenantStore(sheets, "S", MemoriaRedis()).obter_ou_criar("5585999990001", "Zé", "agora")
    assert novo and c.nome == "Zé"
    assert set(sheets.abas) == {"usuarios", "5585999990001_estoque", "5585999990001_movimentacoes"}
    assert sheets.abas["usuarios"] == [["telefone", "nome", "status", "criado_em"],
                                       ["5585999990001", "Zé", "ativo", "agora"]]
    assert sheets.abas["5585999990001_estoque"][0][0] == "produto"


def test_comerciante_existente_nao_e_recriado():
    sheets = FakeSheets()
    TenantStore(sheets, "S", MemoriaRedis()).obter_ou_criar("5585", "Zé", "agora")
    # outra instância, sem cache (ex.: outro servidor): acha na aba usuarios
    c, novo = TenantStore(sheets, "S", MemoriaRedis()).obter_ou_criar("5585", "Zé", "depois")
    assert not novo and c.criado_em == "agora"
    assert len(sheets.abas["usuarios"]) == 2 and sheets.abas_criadas == 3


def test_cache_evita_ir_na_planilha():
    sheets, cache = FakeSheets(), MemoriaRedis()
    store = TenantStore(sheets, "S", cache)
    store.obter_ou_criar("5585", "Zé", "agora")
    sheets.abas.clear()  # se fosse na planilha, quebraria
    c, novo = store.obter_ou_criar("5585", "Zé", "agora")
    assert (c.telefone, novo) == ("5585", False)


def test_garantir_abas_e_idempotente():
    sheets = FakeSheets()
    garantir_abas(sheets, "S", {"a": ["x"], "b": ["y"]})
    garantir_abas(sheets, "S", {"a": ["x"], "b": ["y"]})
    assert sheets.abas_criadas == 2


def _aplicar(repo, acao, qtd, msg_id, produto="arroz", unidade="kg", valor=None):
    i = Intencao(acao=acao, itens=[Item(produto=produto, quantidade=qtd, unidade=unidade, valor_total=valor)])
    r = inventory.aplicar(i, repo.carregar_estoque(), texto="fala", msg_id=msg_id, agora="agora")
    assert r.ok, r.problemas
    repo.salvar(r.movimentos, r.estoque_alterado)


def test_repositorio_ida_e_volta():
    sheets = FakeSheets()
    TenantStore(sheets, "S", MemoriaRedis()).obter_ou_criar("5585", "Zé", "agora")
    repo = SheetsInventoryRepository(sheets, "S", "5585")

    _aplicar(repo, Acao.COMPRA, 300, "m1", valor=300)
    _aplicar(repo, Acao.COMPRA, 5, "m2", produto="feijão")
    _aplicar(repo, Acao.VENDA, 10, "m3")  # atualiza a linha do arroz, não cria outra

    estoque = repo.carregar_estoque()
    assert {k: (p.quantidade, p.linha) for k, p in estoque.items()} == {"arroz": (290, 2), "feijao": (5, 3)}
    assert estoque["arroz"].custo_medio == 1.0
    assert len(sheets.abas["5585_estoque"]) == 3  # cabeçalho + 2 produtos

    movimentos = repo.carregar_movimentos()
    assert [(m.whatsapp_msg_id, m.tipo.value, m.quantidade) for m in movimentos] == [
        ("m1", "entrada", 300), ("m2", "entrada", 5), ("m3", "saida", 10)]


def test_comerciantes_ficam_em_abas_separadas():
    sheets = FakeSheets()
    for tel in ("111", "222"):
        TenantStore(sheets, "S", MemoriaRedis()).obter_ou_criar(tel, "", "agora")
    _aplicar(SheetsInventoryRepository(sheets, "S", "111"), Acao.COMPRA, 1, "m1")
    assert SheetsInventoryRepository(sheets, "S", "222").carregar_estoque() == {}
    assert len(SheetsInventoryRepository(sheets, "S", "111").carregar_estoque()) == 1
