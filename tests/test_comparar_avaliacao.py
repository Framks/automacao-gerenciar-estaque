import json
from pathlib import Path

from app.domain.operacoes import RespostaLLM
from tests.avaliacao.comparar import diferencas


def r(*ops):
    return RespostaLLM.model_validate({"operacoes": list(ops)})


def test_acerto_com_normalizacao_e_tolerancia():
    esperado = [{"tipo": "compra", "itens": [{"produto": "feijão", "quantidade": 10, "unidade": "kg",
                                              "valor_unitario": 10, "valor_total": None}]}]
    assert diferencas(esperado, r({"tipo": "compra", "itens": [{"produto": "Feijao", "quantidade": 10.0,
                                                               "unidade": "quilos", "valor_unitario": 10.004}]})) == []


def test_valor_inventado_e_erro():
    esperado = [{"tipo": "compra", "itens": [{"produto": "carne", "quantidade": 5, "valor_unitario": None,
                                              "valor_total": None}]}]
    assert diferencas(esperado, r({"tipo": "compra", "itens": [{"produto": "carne", "quantidade": 5,
                                                               "valor_total": 100}]}))


def test_tipo_errado_e_contagem():
    esperado = [{"tipo": "completar", "pendencia_id": 12, "campos": {"valor_unitario": 10}}]
    assert "tipo" in diferencas(esperado, r({"tipo": "venda", "itens": [{"produto": "feijão"}]}))[0]
    assert "operação" in diferencas(esperado, r())[0]
    assert diferencas(esperado, r({"tipo": "completar", "pendencia_id": 12, "campos": {"valor_unitario": "10"}})) == []


def test_categoria_e_valor_equivalentes():
    esperado = [{"tipo": "completar", "pendencia_id": 7, "campos": {"categoria": "energia"}}]
    assert diferencas(esperado, r({"tipo": "completar", "pendencia_id": 7, "campos": {"categoria": "luz"}})) == []
    esperado = [{"tipo": "completar", "pendencia_id": 4, "campos": {"valor_total": 100}}]
    assert diferencas(esperado, r({"tipo": "completar", "pendencia_id": 4, "campos": {"valor": 100}})) == []


def test_casos_sao_validos():
    linhas = Path("tests/avaliacao/casos.jsonl").read_text(encoding="utf-8").splitlines()
    casos = [json.loads(l) for l in linhas]
    assert len(casos) >= 60 and len({c["id"] for c in casos}) == len(casos)
    for c in casos:
        r(*c["esperado"])  # o esperado também é um schema v2 válido
