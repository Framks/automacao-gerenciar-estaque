"""Comparação estrutural entre as operações esperadas (casos.jsonl) e as obtidas do interpretador.

Compara só o que o caso declara: tipo (sempre), e quando presentes: produto (pela chave), quantidade, unidade
normalizada, valores (Decimal, tolerância de 1 centavo), pendencia_id, campos, valor, categoria, assunto, periodo.
Um valor declarado como null no esperado precisa vir null (ex.: compra sem valor não pode inventar preço).
"""

from decimal import Decimal

from app.domain.dinheiro import parse_valor
from app.domain.financeiro import normalizar_categoria
from app.domain.operacoes import RespostaLLM
from app.domain.units import chave_produto, normalizar_unidade

TOLERANCIA = Decimal("0.01")
NUMERICOS = {"quantidade", "valor_unitario", "valor_total", "valor"}


def _num_igual(esperado, obtido) -> bool:
    if esperado is None or obtido is None:
        return esperado is None and obtido is None
    try:
        return abs(parse_valor(esperado) - parse_valor(obtido)) <= TOLERANCIA
    except (ValueError, ArithmeticError):
        return False


def _diferencas_item(e: dict, o: dict, prefixo: str) -> list[str]:
    erros = []
    if chave_produto(e["produto"]) != chave_produto(o.get("produto") or ""):
        erros.append(f"{prefixo}produto: esperado {e['produto']!r}, veio {o.get('produto')!r}")
    for campo in ("quantidade", "valor_unitario", "valor_total"):
        if campo in e and not _num_igual(e[campo], o.get(campo)):
            erros.append(f"{prefixo}{campo}: esperado {e[campo]}, veio {o.get(campo)}")
    if "unidade" in e and normalizar_unidade(e["unidade"]) != normalizar_unidade(o.get("unidade")):
        erros.append(f"{prefixo}unidade: esperado {e['unidade']}, veio {o.get('unidade')}")
    return erros


def diferencas(esperado: list[dict], obtido: RespostaLLM) -> list[str]:
    """Lista vazia = acertou."""
    ops = obtido.model_dump(mode="json")["operacoes"]
    if len(ops) != len(esperado):
        return [f"esperava {len(esperado)} operação(ões) ({', '.join(e['tipo'] for e in esperado)}), veio {len(ops)}"
                f" ({', '.join(o['tipo'] for o in ops)})"]
    erros = []
    for n, (e, o) in enumerate(zip(esperado, ops)):
        p = f"op{n}."
        if e["tipo"] != o["tipo"]:
            erros.append(f"{p}tipo: esperado {e['tipo']}, veio {o['tipo']}")
            continue
        if "itens" in e:
            if len(e["itens"]) != len(o["itens"]):
                erros.append(f"{p}itens: esperado {len(e['itens'])}, veio {len(o['itens'])}")
            else:
                for i, (ei, oi) in enumerate(zip(e["itens"], o["itens"])):
                    erros += _diferencas_item(ei, oi, f"{p}item{i}.")
        if "pendencia_id" in e and e["pendencia_id"] != o.get("pendencia_id"):
            erros.append(f"{p}pendencia_id: esperado {e['pendencia_id']}, veio {o.get('pendencia_id')}")
        if "valor" in e and not _num_igual(e["valor"], o.get("valor")):
            erros.append(f"{p}valor: esperado {e['valor']}, veio {o.get('valor')}")
        if "categoria" in e:
            esperada = normalizar_categoria(e["categoria"]) if e["categoria"] else "outros"
            veio = normalizar_categoria(o.get("categoria")) if o.get("categoria") else "outros"
            if esperada != veio:
                erros.append(f"{p}categoria: esperado {e['categoria']}, veio {o.get('categoria')}")
        for campo in ("assunto", "periodo"):
            if campo in e and e[campo] != (o.get(campo) or ("estoque" if campo == "assunto" else "mes")):
                erros.append(f"{p}{campo}: esperado {e[campo]}, veio {o.get(campo)}")
        for chave, valor in (e.get("campos") or {}).items():
            veio = (o.get("campos") or {}).get(chave)
            if chave == "categoria":
                ok = normalizar_categoria(valor) == normalizar_categoria(veio)
            elif chave in NUMERICOS:
                ok = _num_igual(valor, veio)
                if not ok and chave in ("valor_total", "valor"):  # "valor" e "valor_total" são o mesmo dado
                    outro = (o.get("campos") or {}).get("valor" if chave == "valor_total" else "valor_total")
                    ok = _num_igual(valor, outro)
            else:
                ok = str(valor).lower() == str(veio).lower()
            if not ok:
                erros.append(f"{p}campos.{chave}: esperado {valor}, veio {veio}")
    return erros
