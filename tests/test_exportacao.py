"""Exportação CSV: formato para o Excel pt-BR, proteção contra injection, isolamento e ZIP."""

import csv
import io
import zipfile
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from app.db.engine import transacao
from app.db.modelos import ComercianteDB, ConfiguracaoDB, MovimentoDB, ProdutoDB
from app.servicos.exportacao import BOM, ExportacaoInvalida, Exportador, Filtro, fmt_decimal, juntar, seguro


def _linhas(texto: str) -> list[list[str]]:
    assert texto.startswith(BOM)
    return list(csv.reader(io.StringIO(texto[len(BOM):]), delimiter=";"))


@pytest.fixture
def dados(fabrica_pg):
    with transacao(fabrica_pg) as s:
        a = ComercianteDB(telefone="558881061891", nome="Francisco", permitido=True)
        b = ComercianteDB(telefone="558592572430", nome="Atila")
        s.add_all([a, b])
        s.flush()
        p = ProdutoDB(comerciante_id=a.id, nome="feijão", chave="feijao", unidade="kg", quantidade=Decimal("7.5"),
                      custo_medio=Decimal("10.0000"))
        s.add_all([p, ProdutoDB(comerciante_id=b.id, nome="leite", chave="leite", unidade="l", quantidade=0)])
        s.flush()
        s.add_all([
            MovimentoDB(comerciante_id=a.id, produto_id=p.id, origem_id="m1-0", tipo="entrada", produto="feijão",
                        quantidade=Decimal("10"), unidade="kg", valor_total=Decimal("100.5"),
                        texto="=HYPERLINK(\"http://x\")", criado_em=datetime(2026, 10, 29, 13, 42, tzinfo=timezone.utc)),
            MovimentoDB(comerciante_id=a.id, produto_id=p.id, origem_id="m2-0", tipo="saida", produto="feijão",
                        quantidade=Decimal("2.5"), unidade="kg", texto="vendi",
                        criado_em=datetime(2026, 11, 2, 12, 0, tzinfo=timezone.utc)),
            MovimentoDB(comerciante_id=b.id, origem_id="x-0", tipo="entrada", produto="leite", quantidade=3,
                        unidade="l", texto="do outro"),
            ConfiguracaoDB(chave="tts_voz", valor_json="pt-BR-FranciscaNeural", atualizado_por="admin@x"),
        ])
    return Exportador(fabrica_pg), a.id, b.id


def test_formatos():
    assert fmt_decimal(Decimal("100.50")) == "100,5"
    assert fmt_decimal(Decimal("7.000")) == "7"
    assert fmt_decimal(Decimal("0.0001")) == "0,0001"
    assert fmt_decimal(None) == ""
    assert seguro("=1+1") == "'=1+1" and seguro("-5") == "'-5" and seguro("@x") == "'@x" and seguro("ok") == "ok"


def test_movimentos_com_formato_e_injection(dados):
    exp, a, _ = dados
    linhas = _linhas(juntar(exp.csv("movimentos", Filtro(comerciante_id=a))))
    assert linhas[0][:7] == ["data_hora", "tipo", "produto", "quantidade", "unidade", "valor_unitario", "valor_total"]
    assert linhas[1][:7] == ["29/10/2026 10:42", "entrada", "feijão", "10", "kg", "", "100,5"]  # fuso de Fortaleza
    assert linhas[1][-1] == "'=HYPERLINK(\"http://x\")"
    assert len(linhas) == 3  # cabeçalho + 2: nada do outro comerciante


def test_periodo(dados):
    exp, a, _ = dados
    linhas = _linhas(juntar(exp.csv("movimentos", Filtro(comerciante_id=a, de=date(2026, 11, 1), ate=date(2026, 11, 2)))))
    assert [l[1] for l in linhas[1:]] == ["saida"]


def test_isolamento_e_geral(dados):
    exp, a, b = dados
    assert [l[1] for l in _linhas(juntar(exp.csv("estoque", Filtro(comerciante_id=b))))[1:]] == ["leite"]
    assert len(_linhas(juntar(exp.csv("estoque")))) == 3
    assert [l[0] for l in _linhas(juntar(exp.csv("comerciantes")))[1:]] == ["558881061891", "558592572430"]


def test_configuracoes_sao_gerais(dados):
    exp, a, _ = dados
    (cab, linha) = _linhas(juntar(exp.csv("configuracoes", Filtro(comerciante_id=a))))
    assert linha[:3] == ["tts_voz", '"pt-BR-FranciscaNeural"', "global"]


def test_exige_comerciante_e_nome_valido(dados):
    exp, _, _ = dados
    with pytest.raises(ExportacaoInvalida):
        exp.csv("movimentos")
    with pytest.raises(ExportacaoInvalida):
        exp.csv("senhas")


def test_pacote_zip(dados):
    exp, a, _ = dados
    with zipfile.ZipFile(io.BytesIO(exp.pacote_zip(a))) as z:
        assert sorted(z.namelist()) == ["caixa.csv", "estoque.csv", "mensagens.csv", "movimentos.csv", "pendencias.csv"]
        assert "feijão" in z.read("estoque.csv").decode("utf-8")
        assert "leite" not in z.read("estoque.csv").decode("utf-8")
