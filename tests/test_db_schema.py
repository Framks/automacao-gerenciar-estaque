"""Schema do banco (migrações aplicadas num Postgres vazio) e suas restrições."""

from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.db.engine import transacao
from app.db.modelos import ComercianteDB, ConfiguracaoDB, MovimentoDB, ProdutoDB


def _comerciante(s, telefone="5585"):
    c = ComercianteDB(telefone=telefone, nome="Zé")
    s.add(c)
    s.flush()
    return c


def test_insere_e_le_com_decimais(fabrica_pg):
    with transacao(fabrica_pg) as s:
        c = _comerciante(s)
        p = ProdutoDB(comerciante_id=c.id, nome="feijão", chave="feijao", unidade="kg",
                      quantidade=Decimal("10.5"), custo_medio=Decimal("3.33333"))
        s.add(p)
        s.flush()
        s.add(MovimentoDB(comerciante_id=c.id, produto_id=p.id, origem_id="m1-0", tipo="entrada", produto="feijão",
                          quantidade=Decimal("10.5"), unidade="kg", valor_total=Decimal("35.005")))
    with transacao(fabrica_pg) as s:
        p = s.scalar(select(ProdutoDB))
        m = s.scalar(select(MovimentoDB))
        assert (p.quantidade, p.custo_medio) == (Decimal("10.500"), Decimal("3.3333"))
        # numeric(14,2) arredonda "para cima" no meio (35.005 -> 35.01); por isso o domínio já entrega quantizado
        assert m.valor_total == Decimal("35.01")
        assert s.scalar(select(ComercianteDB.status)) == "ativo"


def test_chave_de_produto_unica_por_comerciante(fabrica_pg):
    with transacao(fabrica_pg) as s:
        a, b = _comerciante(s, "1"), _comerciante(s, "2")
        s.add_all([ProdutoDB(comerciante_id=a.id, nome="arroz", chave="arroz", unidade="kg"),
                   ProdutoDB(comerciante_id=b.id, nome="arroz", chave="arroz", unidade="kg")])
    with pytest.raises(IntegrityError):
        with transacao(fabrica_pg) as s:
            s.add(ProdutoDB(comerciante_id=a.id, nome="Arroz", chave="arroz", unidade="kg"))


def test_referencia_precisa_existir_e_tipo_valido(fabrica_pg):
    with transacao(fabrica_pg) as s:
        c = _comerciante(s)
    with pytest.raises(IntegrityError):
        with transacao(fabrica_pg) as s:
            s.add(MovimentoDB(comerciante_id=c.id, origem_id="x", tipo="ajuste_valor", produto="a",
                              quantidade=0, unidade="kg", referencia_id=999))
    with pytest.raises(IntegrityError):
        with transacao(fabrica_pg) as s:
            s.add(MovimentoDB(comerciante_id=c.id, origem_id="y", tipo="roubo", produto="a", quantidade=0, unidade="kg"))


def test_configuracao_global_unica(fabrica_pg):
    with transacao(fabrica_pg) as s:
        s.add(ConfiguracaoDB(chave="tts_voz", valor_json="a"))
    with pytest.raises(IntegrityError):  # NULLS NOT DISTINCT: duas globais da mesma chave não
        with transacao(fabrica_pg) as s:
            s.add(ConfiguracaoDB(chave="tts_voz", valor_json="b"))
