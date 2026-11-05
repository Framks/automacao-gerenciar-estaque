"""Leva os dados do Google Sheets para o Postgres, conferindo os saldos.

1. Lê a aba `usuarios` e, de cada comerciante, `<tel>_estoque` e `<tel>_movimentacoes`.
2. Recalcula o estoque a partir do livro-caixa (mesmas fórmulas do domínio) e compara com a aba `_estoque`.
   Se houver divergência, para e mostra o relatório (nada é gravado).
3. Grava no Postgres de forma idempotente: comerciante por telefone, movimento por (comerciante, id antigo).
   Rodar de novo não duplica nada.

Uso:
  python scripts/migrar_sheets_postgres.py --dry-run          # só mostra o relatório
  python scripts/migrar_sheets_postgres.py --url "$URL_DO_BANCO"
Sem --url, usa BOT_DATABASE_URL ou DATABASE_URL do .env.
"""

import argparse
import sys
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import func, select  # noqa: E402
from sqlalchemy.dialects.postgresql import insert  # noqa: E402

from app.db.engine import fabrica, transacao  # noqa: E402
from app.db.modelos import ComercianteDB, MovimentoDB, ProdutoDB  # noqa: E402
from app.domain.dinheiro import unitario  # noqa: E402
from app.domain.inventory import custo_medio_apos_entrada  # noqa: E402
from app.domain.models import Comerciante, Movimento, ProdutoEstoque, TipoMovimento  # noqa: E402
from app.domain.units import chave_produto  # noqa: E402
from app.storage.pg import para_datetime  # noqa: E402
from app.storage.sheets_repo import ABA_USUARIOS, SheetsInventoryRepository, executar, intervalo  # noqa: E402
from app.whatsapp.client import mascarar, telefone_permitido  # noqa: E402

TOLERANCIA = Decimal("0.001")


@dataclass
class DadosComerciante:
    comerciante: Comerciante
    estoque: dict[str, ProdutoEstoque]
    movimentos: list[Movimento]
    divergencias: list[str] = field(default_factory=list)


def ler_sheets(sheets, spreadsheet_id: str) -> list[DadosComerciante]:
    resposta = executar(sheets.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id, range=intervalo(ABA_USUARIOS, "A2:D"), valueRenderOption="UNFORMATTED_VALUE"
    ))
    dados = []
    for linha in resposta.get("values", []):
        linha = [str(v) for v in linha] + [""] * (4 - len(linha))
        if not linha[0]:
            continue
        repo = SheetsInventoryRepository(sheets, spreadsheet_id, linha[0])
        dados.append(DadosComerciante(
            Comerciante(linha[0], linha[1], linha[2] or "ativo", linha[3]),
            repo.carregar_estoque(),
            repo.carregar_movimentos(),
        ))
    return dados


def recalcular(movimentos: list[Movimento]) -> dict[str, tuple[Decimal, Decimal | None]]:
    """Refaz o estoque {chave: (quantidade, custo_medio)} a partir do livro-caixa, em ordem."""
    estado: dict[str, tuple[Decimal, Decimal | None]] = {}
    entradas: dict[tuple[str, str], Movimento] = {}  # (wa_msg_id, chave) -> entrada, para desfazer
    for m in movimentos:
        chave = chave_produto(m.produto)
        q, custo = estado.get(chave, (Decimal(0), None))
        if m.tipo == TipoMovimento.ENTRADA:
            if m.valor_total is not None:
                custo = custo_medio_apos_entrada(custo, q, m.quantidade, m.valor_total)
            q += m.quantidade
            entradas[(m.whatsapp_msg_id, chave)] = m
        elif m.tipo == TipoMovimento.SAIDA:
            q -= m.quantidade
        elif m.tipo == TipoMovimento.AJUSTE:
            original = entradas.get((m.referencia, chave))
            depois = q + m.quantidade
            if m.quantidade < 0 and original and original.valor_total is not None and custo is not None and depois > 0:
                custo = max(unitario((q * custo - original.valor_total) / depois), Decimal(0))
            q = depois
        estado[chave] = (q, custo)
    return estado


def conferir(d: DadosComerciante) -> list[str]:
    calculado = recalcular(d.movimentos)
    problemas = []
    for chave in sorted(set(calculado) | set(d.estoque)):
        q_calc, c_calc = calculado.get(chave, (Decimal(0), None))
        p = d.estoque.get(chave)
        q_plan, c_plan = (p.quantidade, p.custo_medio) if p else (Decimal(0), None)
        if abs(q_calc - q_plan) > TOLERANCIA:
            problemas.append(f"{chave}: planilha tem {q_plan}, o livro-caixa dá {q_calc}")
        if (c_calc is None) != (c_plan is None) or (c_calc is not None and abs(c_calc - c_plan) > TOLERANCIA):
            problemas.append(f"{chave}: custo na planilha {c_plan}, pelo livro-caixa {c_calc}")
    return problemas


def gravar(fabrica_, dados: list[DadosComerciante], permitidos: str, timezone: str) -> dict[str, int]:
    """Grava e devolve quantas linhas novas entraram em cada tabela."""
    tz = ZoneInfo(timezone)
    antes = contar(fabrica_)
    with transacao(fabrica_) as s:
        for d in dados:
            c = d.comerciante
            s.execute(insert(ComercianteDB).values(
                telefone=c.telefone, nome=c.nome, status=c.status,
                permitido=telefone_permitido(c.telefone, permitidos),
                criado_em=para_datetime(c.criado_em, tz) or datetime.now(tz),
            ).on_conflict_do_nothing(index_elements=["telefone"]))
            cid = s.scalar(select(ComercianteDB.id).where(ComercianteDB.telefone == c.telefone))

            produtos = {}
            for chave, p in d.estoque.items():
                s.execute(insert(ProdutoDB).values(
                    comerciante_id=cid, nome=p.produto, chave=chave, unidade=p.unidade, quantidade=p.quantidade,
                    custo_medio=p.custo_medio, atualizado_em=para_datetime(p.atualizado_em, tz) or datetime.now(tz),
                # do_nothing: rodar de novo depois do corte nunca sobrescreve o saldo atual com o da planilha
                ).on_conflict_do_nothing(constraint="uq_produtos_comerciante_chave"))
                produtos[chave] = s.scalar(select(ProdutoDB.id).where(
                    ProdutoDB.comerciante_id == cid, ProdutoDB.chave == chave))

            for m in d.movimentos:
                s.execute(insert(MovimentoDB).values(
                    comerciante_id=cid, produto_id=produtos.get(chave_produto(m.produto)), origem_id=m.id,
                    tipo=m.tipo.value, produto=m.produto, quantidade=m.quantidade, unidade=m.unidade,
                    valor_total=m.valor_total, valor_unitario=m.valor_unitario, texto=m.texto_transcrito,
                    wa_msg_id=m.whatsapp_msg_id, referencia_msg=m.referencia, autor="migracao-sheets",
                    criado_em=para_datetime(m.data_hora, tz) or datetime.now(tz),
                ).on_conflict_do_nothing(constraint="uq_movimentos_origem"))
    depois = contar(fabrica_)
    return {k: depois[k] - antes[k] for k in depois}


def contar(fabrica_) -> dict[str, int]:
    with transacao(fabrica_) as s:
        return {
            "comerciantes": s.scalar(select(func.count()).select_from(ComercianteDB)),
            "produtos": s.scalar(select(func.count()).select_from(ProdutoDB)),
            "movimentos": s.scalar(select(func.count()).select_from(MovimentoDB)),
        }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--url", help="URL do Postgres (padrão: BOT_DATABASE_URL/DATABASE_URL)")
    args = ap.parse_args()

    from app.config import get_settings
    from app.google_auth import servico_sheets

    s = get_settings()
    dados = ler_sheets(servico_sheets(s), s.spreadsheet_id)

    erro = False
    for d in dados:
        d.divergencias = conferir(d)
        print(f"{mascarar(d.comerciante.telefone)}: {len(d.estoque)} produtos, {len(d.movimentos)} movimentos"
              + (" | DIVERGÊNCIAS: " + "; ".join(d.divergencias) if d.divergencias else " | saldos conferem"))
        erro = erro or bool(d.divergencias)
    origem = {"comerciantes": len(dados), "produtos": sum(len(d.estoque) for d in dados),
              "movimentos": sum(len(d.movimentos) for d in dados)}
    print("origem (Sheets):", origem)
    if erro:
        print("Há divergências: nada foi gravado.")
        return 1
    if args.dry_run:
        print("--dry-run: nada foi gravado.")
        return 0

    f = fabrica(args.url or s.banco_url)
    print("gravados agora:", gravar(f, dados, s.telefones_permitidos, s.timezone))
    destino = contar(f)
    print("destino (Postgres):", destino)
    if destino != origem:
        print("ATENÇÃO: as contagens não batem (o banco já tinha outros dados?)")
        return 1
    print("Migração conferida.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
