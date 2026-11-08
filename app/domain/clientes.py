"""Clientes do fiado: nome falado -> chave estável, e busca tolerante a erros de transcrição. Código puro."""

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher

from app.domain.units import sem_acento

# palavras que não fazem parte do nome ("pro João", "a Dona Maria", "o seu Zé")
_ANTES = {"o", "a", "pro", "pra", "para", "pelo", "pela", "do", "da", "de", "ao", "à"}
_TITULOS = {"dona", "seu", "sr", "sra", "senhor", "senhora", "d"}

SIMILAR_IGUAL = 0.9  # acima disso é o mesmo cliente (erro pequeno de transcrição)
SIMILAR_TALVEZ = 0.75  # entre os dois: pergunta


def _palavras(nome: str) -> list[str]:
    return [p for p in re.split(r"[^\wà-ú]+", (nome or "").strip()) if p]


def nome_cliente(nome: str) -> str:
    """Nome para mostrar: sem "pro"/"a" no começo, com iniciais maiúsculas ("pra dona maria" -> "Dona Maria")."""
    palavras = _palavras(nome)
    while palavras and palavras[0].lower() in _ANTES:
        palavras.pop(0)
    return " ".join(p if any(c.isupper() for c in p[1:]) else p.capitalize() for p in palavras)


def chave_cliente(nome: str) -> str:
    """Chave de busca: sem acento, minúsculas, sem "pro", "dona", "seu" ("Dona Maria" e "maria" são a mesma)."""
    palavras = [sem_acento(p.lower()) for p in _palavras(nome)]
    while palavras and (palavras[0] in _ANTES or palavras[0] in _TITULOS):
        palavras.pop(0)
    return " ".join(palavras)


def similaridade(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    ta, tb = a.split(), b.split()
    curto, longo = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
    if longo[:len(curto)] == curto:  # "joao" e "joao pedro": parecidos, mas não o mesmo
        return 0.8
    return SequenceMatcher(None, a, b).ratio()


@dataclass
class Resolucao:
    chave: str
    nome: str  # nome para gravar/mostrar
    existente: bool  # já está na lista do fiado
    sugestoes: list[str] = field(default_factory=list)  # nomes parecidos, quando não dá para decidir sozinho


def resolver(nome: str, clientes: dict, *, estrito: bool = False) -> Resolucao:
    """Acha o cliente pelo nome falado.

    - chave igual: é ele;
    - um único parecido acima de SIMILAR_IGUAL: é ele (ex.: "Joao" transcrito como "Joam");
    - parecidos acima de SIMILAR_TALVEZ: devolve `sugestoes` para perguntar (a não ser `estrito`, quando o
      comerciante já respondeu a pergunta: aí vira cliente novo);
    - nenhum: cliente novo.
    `clientes`: {chave: objeto com .nome}.
    """
    chave = chave_cliente(nome)
    if chave in clientes:
        return Resolucao(chave, clientes[chave].nome, True)
    notas = sorted(((similaridade(chave, k), k) for k in clientes), reverse=True)
    iguais = [k for nota, k in notas if nota >= SIMILAR_IGUAL]
    if len(iguais) == 1:
        return Resolucao(iguais[0], clientes[iguais[0]].nome, True)
    talvez = [clientes[k].nome for nota, k in notas if nota >= SIMILAR_TALVEZ]
    if talvez and not estrito:
        return Resolucao(chave, nome_cliente(nome), False, talvez[:3])
    return Resolucao(chave, nome_cliente(nome), False)


def buscar_existente(nome: str, clientes: dict) -> tuple[str | None, list[str]]:
    """Para pagamento/consulta: só clientes que já existem. (chave achada, ou None e os nomes parecidos)."""
    r = resolver(nome, clientes)
    if r.existente:
        return r.chave, []
    return None, r.sugestoes
