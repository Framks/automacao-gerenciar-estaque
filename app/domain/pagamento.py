"""Meio de pagamento (dinheiro, Pix, cartão, fiado). Código puro.

A IA só copia o que foi falado; a normalização e o padrão do comerciante ficam aqui.
"""

from app.domain.units import sem_acento

FORMAS = ("dinheiro", "pix", "cartao", "fiado")
NAO_INFORMADO = "nao_informado"
TODAS = (*FORMAS, NAO_INFORMADO)

# como a forma aparece na resposta falada ("Recebido no Pix.")
FALADA = {"dinheiro": "em dinheiro", "pix": "no Pix", "cartao": "no cartão", "fiado": "fiado"}
NOME = {"dinheiro": "dinheiro", "pix": "Pix", "cartao": "cartão", "fiado": "fiado", NAO_INFORMADO: "não informado"}

_SINONIMOS = {
    "dinheiro": "dinheiro", "especie": "dinheiro", "em especie": "dinheiro", "dinheiro vivo": "dinheiro",
    "cash": "dinheiro", "a vista": "dinheiro", "avista": "dinheiro", "em dinheiro": "dinheiro",
    "pix": "pix", "no pix": "pix", "piks": "pix", "pics": "pix", "transferencia": "pix",
    "cartao": "cartao", "no cartao": "cartao", "debito": "cartao", "no debito": "cartao", "credito": "cartao",
    "no credito": "cartao", "maquininha": "cartao", "na maquininha": "cartao", "cartao de credito": "cartao",
    "cartao de debito": "cartao",
    "fiado": "fiado", "na caderneta": "fiado", "caderneta": "fiado", "pendurado": "fiado", "pendura": "fiado",
    "na conta": "fiado", "a prazo": "fiado", "prazo": "fiado", "pra pagar depois": "fiado",
    "nao_informado": NAO_INFORMADO, "nao informado": NAO_INFORMADO,
}


def normalizar_pagamento(texto: str | None) -> str | None:
    """Uma das FORMAS (ou nao_informado). None se vazio ou desconhecido (a IA não deve inventar)."""
    if texto is None:
        return None
    chave = " ".join(sem_acento(str(texto).lower()).replace("_", " ").split())
    if not chave:
        return None
    if chave in _SINONIMOS:
        return _SINONIMOS[chave]
    return _SINONIMOS.get(chave.replace(" ", "_"))


def resolver_forma(dita: str | None, padrao: str = NAO_INFORMADO) -> tuple[str, str]:
    """(forma, origem): `dito` se a fala disse; senão o padrão do comerciante (`padrao`) ou nao_informado ("")."""
    forma = normalizar_pagamento(dita)
    if forma and forma != NAO_INFORMADO:
        return forma, "dito"
    padrao = normalizar_pagamento(padrao) or NAO_INFORMADO
    if padrao not in (NAO_INFORMADO, "fiado"):  # fiado nunca é presumido: precisa de um cliente
        return padrao, "padrao"
    return NAO_INFORMADO, ""


def lista_falada(formas: tuple[str, ...] | list[str]) -> str:
    """"dinheiro, Pix, cartão ou fiado" """
    nomes = [NOME[f] for f in formas if f in NOME and f != NAO_INFORMADO]
    if len(nomes) <= 1:
        return "".join(nomes)
    return ", ".join(nomes[:-1]) + " ou " + nomes[-1]
