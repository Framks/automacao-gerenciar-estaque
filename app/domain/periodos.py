"""Datas e horários falados -> datas de verdade, no fuso do comerciante. Código puro (a IA não calcula datas).

- `resolver_periodo("ontem" | "semana" | "mes" | "mes_passado" | "dia:3" | "mes:10" | "data:2026-11-03" ...)`
- `parse_horario("8 da noite" | "20h" | "19:30" | "oito e meia da noite" | "meio-dia")`
"""

import calendar
import re
from dataclasses import dataclass
from datetime import date, timedelta

from app.domain.units import sem_acento

MESES = ["janeiro", "fevereiro", "marco", "abril", "maio", "junho", "julho", "agosto", "setembro", "outubro",
         "novembro", "dezembro"]
NOMES_MES = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto", "setembro", "outubro",
             "novembro", "dezembro"]


@dataclass(frozen=True)
class Periodo:
    inicio: date
    fim: date  # inclusive
    rotulo: str  # "hoje (08/11)", "outubro de 2026"
    tipo: str  # dia | semana | mes

    @property
    def quando(self) -> str:
        """Para frases: "hoje", "ontem", "no dia 05/11", "nesta semana", "neste mês", "em outubro"."""
        r = self.rotulo
        if r.startswith(("hoje", "ontem")):
            return r.split(" (")[0]
        if self.tipo == "dia":
            return f"no dia {r}"
        if self.tipo == "semana":
            return "nesta semana" if r.startswith("esta") else "na semana passada"
        if "(até" in r:
            return "neste mês"
        return f"em {r}"

    @property
    def dias(self) -> list[date]:
        return [self.inicio + timedelta(days=i) for i in range((self.fim - self.inicio).days + 1)]


def _dm(d: date) -> str:
    return d.strftime("%d/%m")


def _dia(d: date, hoje: date, rotulo: str | None = None) -> Periodo:
    if rotulo is None:
        rotulo = {0: "hoje", 1: "ontem"}.get((hoje - d).days, "")
        rotulo = f"{rotulo} ({_dm(d)})" if rotulo else _dm(d)
    return Periodo(d, d, rotulo, "dia")


def _mes(ano: int, mes: int, hoje: date) -> Periodo:
    inicio = date(ano, mes, 1)
    fim = date(ano, mes, calendar.monthrange(ano, mes)[1])
    if (ano, mes) == (hoje.year, hoje.month):
        return Periodo(inicio, hoje, f"{NOMES_MES[mes - 1]} (até {_dm(hoje)})", "mes")
    return Periodo(inicio, fim, f"{NOMES_MES[mes - 1]} de {ano}", "mes")


def _mes_recente(mes: int, hoje: date) -> Periodo:
    """O mês com esse número mais recente que não está no futuro ("outubro" em março = outubro do ano passado)."""
    ano = hoje.year if mes <= hoje.month else hoje.year - 1
    return _mes(ano, mes, hoje)


def mes_anterior(hoje: date) -> Periodo:
    ultimo = hoje.replace(day=1) - timedelta(days=1)
    return _mes(ultimo.year, ultimo.month, hoje)


def resolver_periodo(texto: str | None, hoje: date) -> Periodo:
    t = " ".join(sem_acento((texto or "").lower()).replace("_", " ").split())
    if t in ("", "hoje", "dia", "do dia"):
        return _dia(hoje, hoje)
    if t == "ontem":
        return _dia(hoje - timedelta(days=1), hoje)
    if t == "anteontem":
        return _dia(hoje - timedelta(days=2), hoje)
    if t in ("semana", "esta semana", "essa semana"):
        inicio = hoje - timedelta(days=hoje.weekday())
        return Periodo(inicio, hoje, f"esta semana ({_dm(inicio)} a {_dm(hoje)})", "semana")
    if t in ("semana passada", "ultima semana"):
        fim = hoje - timedelta(days=hoje.weekday() + 1)
        inicio = fim - timedelta(days=6)
        return Periodo(inicio, fim, f"semana passada ({_dm(inicio)} a {_dm(fim)})", "semana")
    if t in ("mes", "este mes", "esse mes", "mes atual"):
        return _mes(hoje.year, hoje.month, hoje)
    if t in ("mes passado", "ultimo mes", "mes anterior"):
        return mes_anterior(hoje)

    m = re.fullmatch(r"(?:data:?\s*)?(\d{4})-(\d{2})-(\d{2})", t)
    if m:
        d = date(int(m[1]), int(m[2]), int(m[3]))
        return _dia(min(d, hoje), hoje)
    m = re.fullmatch(r"(?:data:?\s*)?(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?", t)
    if m:
        ano = int(m[3]) if m[3] else hoje.year
        ano = ano + 2000 if ano < 100 else ano
        d = date(ano, int(m[2]), int(m[1]))
        if not m[3] and d > hoje:
            d = d.replace(year=d.year - 1)
        return _dia(d, hoje)
    m = re.fullmatch(r"(?:dia:?\s*)?(\d{1,2})", t)
    if m:
        dia = int(m[1])
        base = hoje if dia <= hoje.day else hoje.replace(day=1) - timedelta(days=1)
        dia = min(dia, calendar.monthrange(base.year, base.month)[1])
        return _dia(base.replace(day=dia), hoje)
    m = re.fullmatch(r"(?:mes:?\s*)?(\d{4})-(\d{1,2})", t)
    if m:
        return _mes(int(m[1]), int(m[2]), hoje)
    m = re.fullmatch(r"mes:?\s*(\d{1,2})", t)
    if m and 1 <= int(m[1]) <= 12:
        return _mes_recente(int(m[1]), hoje)
    for i, nome in enumerate(MESES):
        if re.search(rf"\b{nome}\b", t):
            ano = re.search(r"\b(20\d\d)\b", t)
            return _mes(int(ano[1]), i + 1, hoje) if ano else _mes_recente(i + 1, hoje)
    return _dia(hoje, hoje)


# --- horário -------------------------------------------------------------------------------------------

_NUMEROS = {"uma": 1, "um": 1, "duas": 2, "dois": 2, "tres": 3, "quatro": 4, "cinco": 5, "seis": 6, "sete": 7,
            "oito": 8, "nove": 9, "dez": 10, "onze": 11, "doze": 12, "treze": 13, "catorze": 14, "quatorze": 14,
            "quinze": 15, "dezesseis": 16, "dezessete": 17, "dezoito": 18, "dezenove": 19, "vinte": 20,
            "vinte e uma": 21, "vinte e um": 21, "vinte e duas": 22, "vinte e dois": 22, "vinte e tres": 23}
_DESLIGAR = {"desligar", "desliga", "desligado", "nao", "parar", "para", "nenhum", "off", "cancelar", "nunca"}
INTERVALO_MINUTOS = 15


@dataclass(frozen=True)
class Horario:
    hhmm: str | None = None  # "20:00" (já arredondado para o intervalo do agendador)
    desligar: bool = False
    ambiguo: int | None = None  # "às 8": a hora sem dizer se é manhã ou noite


def _arredondar(h: int, m: int) -> str:
    total = round((h * 60 + m) / INTERVALO_MINUTOS) * INTERVALO_MINUTOS
    total = min(total, 23 * 60 + 60 - INTERVALO_MINUTOS)
    return f"{total // 60:02d}:{total % 60:02d}"


def parse_horario(texto: str | None) -> Horario | None:
    """None se não deu para entender."""
    t = " ".join(sem_acento((texto or "").lower()).replace("-", " ").split())
    if not t:
        return None
    if t in _DESLIGAR or t.startswith(("desliga", "para de", "nao manda", "nao quero")):
        return Horario(desligar=True)
    if "meio dia" in t:
        return Horario(_arredondar(12, 30 if "meia" in t.replace("meio dia", "") else 0))
    if "meia noite" in t:
        return Horario("00:00")

    for palavra, n in sorted(_NUMEROS.items(), key=lambda kv: -len(kv[0])):
        t = re.sub(rf"\b{palavra}\b", str(n), t)
    m = re.search(r"(\d{1,2})\s*(?:h|:|horas?)?\s*(\d{2})?", t)
    if not m:
        return None
    h, minutos = int(m[1]), int(m[2] or 0)
    if re.search(r"\be meia\b", t):
        minutos = 30
    elif re.search(r"\be (15|quinze)\b", t):
        minutos = 15
    if h > 23 or minutos > 59:
        return None

    tarde = re.search(r"\b(da|de) (tarde|noite)\b|\bpm\b", t)
    manha = re.search(r"\b(da|de) (manha|madrugada)\b|\bam\b", t)
    if tarde and h < 12:
        h += 12
    elif manha and h == 12:
        h = 0
    elif not tarde and not manha and 1 <= h <= 11 and not m[2] and ":" not in t:
        return Horario(ambiguo=h)
    return Horario(_arredondar(h, minutos))


def falar_horario(hhmm: str) -> str:
    """"20:00" -> "20h"; "19:30" -> "19h30" """
    h, m = hhmm.split(":")
    return f"{int(h)}h" + ("" if m == "00" else m)
