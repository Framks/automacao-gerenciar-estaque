"""Edição de estoque pelo admin: sempre por ajuste no livro-caixa (com autor e motivo), nunca reescrevendo."""

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.modelos import AuditoriaDB, MovimentoDB, ProdutoDB
from app.domain.dinheiro import quantidade as q3
from app.domain.dinheiro import unitario


class AjusteInvalido(ValueError):
    pass


@dataclass
class ResultadoAjuste:
    produto_id: int
    quantidade: Decimal
    custo_medio: Decimal | None
    movimentos: list[int]


def auditar(s: Session, admin_id: int | None, admin_email: str, acao: str, entidade: str, entidade_id,
            diff: dict | None = None) -> None:
    s.add(AuditoriaDB(admin_id=admin_id, admin_email=admin_email, acao=acao, entidade=entidade,
                      entidade_id=str(entidade_id), diff_json=diff))


def ajustar_produto(s: Session, comerciante_id: int, produto_id: int, *, motivo: str, autor: str,
                    quantidade_nova: Decimal | None = None, custo_medio_novo: Decimal | None = None
                    ) -> ResultadoAjuste:
    motivo = (motivo or "").strip()
    if not motivo:
        raise AjusteInvalido("informe o motivo do ajuste")
    if quantidade_nova is None and custo_medio_novo is None:
        raise AjusteInvalido("informe a quantidade nova e/ou o custo médio novo")
    produto = s.scalar(select(ProdutoDB).where(ProdutoDB.id == produto_id, ProdutoDB.comerciante_id == comerciante_id)
                       .with_for_update())
    if produto is None:
        raise LookupError("produto não encontrado")

    agora = datetime.now(timezone.utc)
    lote = f"admin-{uuid.uuid4().hex[:12]}"
    movimentos = []
    if quantidade_nova is not None:
        nova = q3(quantidade_nova)
        if nova < 0:
            raise AjusteInvalido("a quantidade não pode ser negativa")
        delta = nova - produto.quantidade
        if delta:
            m = MovimentoDB(comerciante_id=comerciante_id, produto_id=produto.id, origem_id=f"{lote}-q", tipo="ajuste",
                            produto=produto.nome, quantidade=delta, unidade=produto.unidade, autor=autor,
                            motivo=motivo, criado_em=agora)
            s.add(m)
            s.flush()
            movimentos.append(m.id)
            produto.quantidade = nova
    if custo_medio_novo is not None:
        custo = unitario(custo_medio_novo)
        if custo < 0:
            raise AjusteInvalido("o custo não pode ser negativo")
        if custo != produto.custo_medio:
            m = MovimentoDB(comerciante_id=comerciante_id, produto_id=produto.id, origem_id=f"{lote}-c",
                            tipo="ajuste_valor", produto=produto.nome, quantidade=0, unidade=produto.unidade,
                            valor_unitario=custo, autor=autor, motivo=motivo, criado_em=agora)
            s.add(m)
            s.flush()
            movimentos.append(m.id)
            produto.custo_medio = custo
    produto.atualizado_em = agora
    return ResultadoAjuste(produto.id, produto.quantidade, produto.custo_medio, movimentos)
