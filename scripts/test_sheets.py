"""Fase 1 — testa o acesso à planilha: cadastra o comerciante "teste000", grava compra e venda e lê de volta.

Requer GOOGLE_SERVICE_ACCOUNT_JSON e SPREADSHEET_ID no .env, com a planilha compartilhada
com o client_email da conta de serviço como Editor.
Depois de testar, apague as abas "teste000_*" e a linha "teste000" da aba usuarios, se quiser.

Uso:  python scripts/test_sheets.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings  # noqa: E402
from app.domain import inventory, replies  # noqa: E402
from app.domain.models import Acao, Intencao, Item  # noqa: E402
from app.fila.controle import MemoriaRedis  # noqa: E402
from app.google_auth import servico_sheets  # noqa: E402
from app.storage.sheets_repo import SheetsInventoryRepository  # noqa: E402
from app.storage.tenants import TenantStore  # noqa: E402

AGORA = "2026-01-01 10:00:00"
TELEFONE = "teste000"

s = get_settings()
sheets = servico_sheets(s)

comerciante, novo = TenantStore(sheets, s.spreadsheet_id, MemoriaRedis()).obter_ou_criar(TELEFONE, "Teste", AGORA)
print(f"Comerciante {'criado' if novo else 'já existia'}: https://docs.google.com/spreadsheets/d/{s.spreadsheet_id}")

repo = SheetsInventoryRepository(sheets, s.spreadsheet_id, TELEFONE)
for i, (acao, qtd, valor) in enumerate([(Acao.COMPRA, 300, 300.0), (Acao.VENDA, 5, 25.0)]):
    estoque = repo.carregar_estoque()
    intencao = Intencao(acao=acao, itens=[Item(produto="arroz", quantidade=qtd, unidade="kg", valor_total=valor)])
    resultado = inventory.aplicar(intencao, estoque, texto="(teste)", msg_id=f"teste-{i}", agora=AGORA)
    repo.salvar(resultado.movimentos, resultado.estoque_alterado)
    print(replies.montar(resultado))

print("Estoque lido de volta:", repo.carregar_estoque())
print("Movimentos:", len(repo.carregar_movimentos()))
