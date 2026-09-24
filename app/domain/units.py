"""Normalização de nomes de produto e unidades, e como falar cada unidade."""

import unicodedata

_UNIDADES = {
    "kg": "kg", "quilo": "kg", "quilos": "kg", "kilo": "kg", "kilos": "kg", "quilograma": "kg", "quilogramas": "kg",
    "g": "g", "grama": "g", "gramas": "g",
    "l": "l", "litro": "l", "litros": "l",
    "ml": "ml", "mililitro": "ml", "mililitros": "ml",
    "un": "un", "und": "un", "unidade": "un", "unidades": "un",
    "saco": "saco", "sacos": "saco",
    "cx": "cx", "caixa": "cx", "caixas": "cx",
    "pct": "pct", "pacote": "pct", "pacotes": "pct",
    "fardo": "fardo", "fardos": "fardo",
    "duzia": "duzia", "duzias": "duzia",
}

# (singular, plural) para a resposta falada
_FALADO = {
    "kg": ("quilo", "quilos"),
    "g": ("grama", "gramas"),
    "l": ("litro", "litros"),
    "ml": ("mililitro", "mililitros"),
    "un": ("unidade", "unidades"),
    "saco": ("saco", "sacos"),
    "cx": ("caixa", "caixas"),
    "pct": ("pacote", "pacotes"),
    "fardo": ("fardo", "fardos"),
    "duzia": ("dúzia", "dúzias"),
}


def sem_acento(texto: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", texto) if unicodedata.category(c) != "Mn")


def chave_produto(nome: str) -> str:
    """Chave usada para comparar produtos: minúsculas, sem acento, espaços simples."""
    return " ".join(sem_acento(nome).lower().split())


def nome_produto(nome: str) -> str:
    """Nome que vai para a planilha e para a fala: minúsculas, mantendo acentos."""
    return " ".join(nome.lower().split())


def normalizar_unidade(unidade: str | None) -> str | None:
    if not unidade:
        return None
    chave = sem_acento(unidade).lower().strip().rstrip(".")
    return _UNIDADES.get(chave, chave)


def unidade_falada(unidade: str, quantidade: float) -> str:
    singular, plural = _FALADO.get(unidade, (unidade, unidade))
    return singular if abs(quantidade) == 1 else plural
