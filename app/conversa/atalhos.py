"""Pedidos tão claros que não precisam da IA (economiza tokens do Groq): hoje, só o fechamento do caixa.

Conservador de propósito: só falas curtas, e nada que fale de horário, de envio automático ou de mês
(isso é configurar ou relatório mensal, e vai para a IA).
"""

import re

from app.domain.operacoes import Operacao, RespostaLLM, TipoOperacao
from app.domain.units import sem_acento

_FECHAMENTO = re.compile(r"^(me )?(manda|mande|envia|ver|quero|faz|faca|fecha|fechar|fechamento|como foi)?\s*"
                         r"(o |a )?(fechamento|fecha o caixa|fechar o caixa|fechar caixa|caixa)"
                         r"( do caixa| de caixa)?( de| do)?( hoje| dia| de hoje| ontem| de ontem)?$")
_NAO = re.compile(r"\b(todo|toda|todos|as|hora|horas|\d+h|noite|manha|tarde|mes|para de|desliga|nao)\b")


def atalho(texto: str | None) -> RespostaLLM | None:
    t = " ".join(re.sub(r"[^\w ]", " ", sem_acento((texto or "").lower())).split())
    if not t or len(t) > 40 or _NAO.search(t) or not _FECHAMENTO.match(t):
        return None
    if "caixa" not in t and "fechamento" not in t:
        return None
    periodo = "ontem" if "ontem" in t else "hoje"
    return RespostaLLM(operacoes=[Operacao(tipo=TipoOperacao.CONSULTA, assunto="caixa", periodo=periodo,
                                           formato="lista")])
