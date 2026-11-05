"""Exportação CSV de todos os dados (comerciantes, estoque, livro-caixa, caixa, pendências, mensagens e config).

Formato pensado para o Excel em português: separador ";", decimal com vírgula, UTF-8 com BOM e datas
"dd/mm/aaaa hh:mm" no fuso configurado. Valores que começam com = + - @ ganham um apóstrofo na frente
(proteção contra CSV injection: as transcrições são texto livre).

Cada exportação é declarada numa tabela (EXPORTACOES), sem if espalhado. O resultado sai em pedaços
(streaming), e o `pacote` junta tudo de um comerciante num ZIP.
"""

import csv
import io
import zipfile
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import Select, select
from sqlalchemy.orm import Session, sessionmaker

from app.db.engine import transacao
from app.db.modelos import (
    ComercianteDB,
    ConfiguracaoDB,
    FeatureFlagDB,
    LancamentoDB,
    MensagemDB,
    MovimentoDB,
    PendenciaDB,
    ProdutoDB,
)

BOM = "﻿"
PERIGOSOS = ("=", "+", "-", "@", "\t", "\r")
LOTE = 500


class ExportacaoInvalida(ValueError):
    pass


@dataclass(frozen=True)
class Filtro:
    comerciante_id: int | None = None
    de: date | None = None
    ate: date | None = None  # inclusivo
    status: str | None = None


@dataclass(frozen=True)
class Exportacao:
    nome: str
    colunas: list[str]
    consulta: Callable[[Filtro, ZoneInfo], Select]
    linha: Callable[[tuple, ZoneInfo], list]
    por_comerciante: bool = True  # aceita filtro de comerciante
    exige_comerciante: bool = False
    tem_periodo: bool = False


def fmt_decimal(valor: Decimal | None) -> str:
    if valor is None:
        return ""
    texto = format(valor, "f")
    if "." in texto:
        texto = texto.rstrip("0").rstrip(".")
    return texto.replace(".", ",")


def fmt_data(momento: datetime | None, tz: ZoneInfo) -> str:
    return momento.astimezone(tz).strftime("%d/%m/%Y %H:%M") if momento else ""


def seguro(valor) -> str:
    """Texto para a célula, sem virar fórmula no Excel."""
    if valor is None:
        return ""
    if isinstance(valor, bool):
        return "sim" if valor else "não"
    texto = str(valor)
    return "'" + texto if texto.startswith(PERIGOSOS) else texto


def _periodo(consulta: Select, coluna, filtro: Filtro, tz: ZoneInfo) -> Select:
    if filtro.de:
        consulta = consulta.where(coluna >= datetime.combine(filtro.de, time.min, tz))
    if filtro.ate:
        consulta = consulta.where(coluna < datetime.combine(filtro.ate + timedelta(days=1), time.min, tz))
    return consulta


def _do_comerciante(consulta: Select, coluna, filtro: Filtro) -> Select:
    return consulta.where(coluna == filtro.comerciante_id) if filtro.comerciante_id is not None else consulta


# --- consultas -------------------------------------------------------------------------------------------

def _q_comerciantes(f: Filtro, tz) -> Select:
    q = select(ComercianteDB.telefone, ComercianteDB.nome, ComercianteDB.status, ComercianteDB.permitido,
               ComercianteDB.criado_em).order_by(ComercianteDB.id)
    return _do_comerciante(q, ComercianteDB.id, f)


def _q_estoque(f: Filtro, tz) -> Select:
    q = select(ComercianteDB.telefone, ProdutoDB.nome, ProdutoDB.unidade, ProdutoDB.quantidade,
               ProdutoDB.custo_medio, ProdutoDB.preco_venda, ProdutoDB.atualizado_em) \
        .join(ComercianteDB, ComercianteDB.id == ProdutoDB.comerciante_id).order_by(ComercianteDB.id, ProdutoDB.nome)
    return _do_comerciante(q, ProdutoDB.comerciante_id, f)


def _q_movimentos(f: Filtro, tz) -> Select:
    ref = MovimentoDB.__table__.alias("ref")
    q = select(MovimentoDB.criado_em, MovimentoDB.tipo, MovimentoDB.produto, MovimentoDB.quantidade,
               MovimentoDB.unidade, MovimentoDB.valor_unitario, MovimentoDB.valor_total,
               MovimentoDB.referencia_msg, ref.c.origem_id, MovimentoDB.autor, MovimentoDB.motivo, MovimentoDB.texto) \
        .outerjoin(ref, ref.c.id == MovimentoDB.referencia_id).order_by(MovimentoDB.criado_em, MovimentoDB.id)
    return _periodo(_do_comerciante(q, MovimentoDB.comerciante_id, f), MovimentoDB.criado_em, f, tz)


def _q_caixa(f: Filtro, tz) -> Select:
    q = select(LancamentoDB.criado_em, LancamentoDB.tipo, LancamentoDB.categoria, LancamentoDB.descricao,
               LancamentoDB.valor, LancamentoDB.forma_pagamento, LancamentoDB.referencia_id, LancamentoDB.texto) \
        .order_by(LancamentoDB.criado_em, LancamentoDB.id)
    return _periodo(_do_comerciante(q, LancamentoDB.comerciante_id, f), LancamentoDB.criado_em, f, tz)


def _q_pendencias(f: Filtro, tz) -> Select:
    q = select(PendenciaDB.criado_em, ComercianteDB.telefone, PendenciaDB.tipo, PendenciaDB.campos_faltando,
               PendenciaDB.resumo, PendenciaDB.status, PendenciaDB.expira_em, PendenciaDB.resolvida_em) \
        .join(ComercianteDB, ComercianteDB.id == PendenciaDB.comerciante_id).order_by(PendenciaDB.id)
    if f.status:
        q = q.where(PendenciaDB.status == f.status)
    return _do_comerciante(q, PendenciaDB.comerciante_id, f)


def _q_mensagens(f: Filtro, tz) -> Select:
    q = select(MensagemDB.criado_em, MensagemDB.direcao, MensagemDB.tipo, MensagemDB.texto, MensagemDB.transcricao,
               MensagemDB.status, MensagemDB.etapa_erro, MensagemDB.erro, MensagemDB.status_entrega) \
        .order_by(MensagemDB.criado_em, MensagemDB.id)
    return _periodo(_do_comerciante(q, MensagemDB.comerciante_id, f), MensagemDB.criado_em, f, tz)


def _q_configuracoes(f: Filtro, tz) -> Select:
    return select(ConfiguracaoDB.chave, ConfiguracaoDB.valor_json, ComercianteDB.telefone, ConfiguracaoDB.atualizado_em,
                  ConfiguracaoDB.atualizado_por) \
        .outerjoin(ComercianteDB, ComercianteDB.id == ConfiguracaoDB.comerciante_id) \
        .order_by(ConfiguracaoDB.chave, ConfiguracaoDB.id)


def _q_flags(f: Filtro, tz) -> Select:
    return select(FeatureFlagDB.chave, FeatureFlagDB.ligado, ComercianteDB.telefone, FeatureFlagDB.atualizado_em,
                  FeatureFlagDB.atualizado_por) \
        .outerjoin(ComercianteDB, ComercianteDB.id == FeatureFlagDB.comerciante_id) \
        .order_by(FeatureFlagDB.chave, FeatureFlagDB.id)


def _escopo(telefone: str | None) -> str:
    return f"comerciante {telefone}" if telefone else "global"


def _json_curto(valor) -> str:
    import json

    return json.dumps(valor, ensure_ascii=False)


EXPORTACOES: dict[str, Exportacao] = {e.nome: e for e in [
    Exportacao(
        "comerciantes", ["telefone", "nome", "status", "permitido", "criado_em"], _q_comerciantes,
        lambda r, tz: [r[0], r[1], r[2], r[3], fmt_data(r[4], tz)],
    ),
    Exportacao(
        "estoque", ["comerciante", "produto", "unidade", "quantidade", "custo_medio", "preco_venda", "atualizado_em"],
        _q_estoque,
        lambda r, tz: [r[0], r[1], r[2], fmt_decimal(r[3]), fmt_decimal(r[4]), fmt_decimal(r[5]), fmt_data(r[6], tz)],
    ),
    Exportacao(
        "movimentos", ["data_hora", "tipo", "produto", "quantidade", "unidade", "valor_unitario", "valor_total",
                       "desfaz_mensagem", "corrige_movimento", "autor", "motivo", "texto"],
        _q_movimentos,
        lambda r, tz: [fmt_data(r[0], tz), r[1], r[2], fmt_decimal(r[3]), r[4], fmt_decimal(r[5]),
                       fmt_decimal(r[6]), r[7], r[8] or "", r[9] or "bot", r[10], r[11]],
        exige_comerciante=True, tem_periodo=True,
    ),
    Exportacao(
        "caixa", ["data_hora", "tipo", "categoria", "descricao", "valor", "forma_pagamento", "referencia", "texto"],
        _q_caixa,
        lambda r, tz: [fmt_data(r[0], tz), r[1], r[2], r[3], fmt_decimal(r[4]), r[5], r[6] or "", r[7]],
        exige_comerciante=True, tem_periodo=True,
    ),
    Exportacao(
        "pendencias", ["criado_em", "comerciante", "tipo", "campos_faltando", "resumo", "status", "expira_em",
                       "resolvida_em"],
        _q_pendencias,
        lambda r, tz: [fmt_data(r[0], tz), r[1], r[2], ", ".join(r[3] or []), r[4], r[5], fmt_data(r[6], tz),
                       fmt_data(r[7], tz)],
    ),
    Exportacao(
        "mensagens", ["data_hora", "direcao", "tipo", "texto", "transcricao", "status", "etapa_erro", "erro",
                      "status_entrega"],
        _q_mensagens,
        lambda r, tz: [fmt_data(r[0], tz), *r[1:]],
        exige_comerciante=True, tem_periodo=True,
    ),
    Exportacao(
        "configuracoes", ["chave", "valor", "escopo", "atualizado_em", "atualizado_por"], _q_configuracoes,
        lambda r, tz: [r[0], _json_curto(r[1]), _escopo(r[2]), fmt_data(r[3], tz), r[4]],
        por_comerciante=False,
    ),
    Exportacao(
        "feature_flags", ["chave", "ligado", "escopo", "atualizado_em", "atualizado_por"], _q_flags,
        lambda r, tz: [r[0], r[1], _escopo(r[2]), fmt_data(r[3], tz), r[4]],
        por_comerciante=False,
    ),
]}

PACOTE = ["estoque", "movimentos", "caixa", "pendencias", "mensagens"]  # o que vai no ZIP de um comerciante


class Exportador:
    def __init__(self, fabrica: sessionmaker[Session], timezone: str = "America/Fortaleza"):
        self._fabrica = fabrica
        self._tz = ZoneInfo(timezone)

    def csv(self, nome: str, filtro: Filtro = Filtro()) -> Iterator[str]:
        """Pedaços de texto do CSV (o primeiro traz o BOM e o cabeçalho)."""
        exp = EXPORTACOES.get(nome)
        if exp is None:
            raise ExportacaoInvalida(f"exportação desconhecida: {nome}")
        if exp.exige_comerciante and filtro.comerciante_id is None:
            raise ExportacaoInvalida(f"a exportação {nome} é por comerciante")
        if not exp.por_comerciante and filtro.comerciante_id is not None:
            filtro = Filtro(de=filtro.de, ate=filtro.ate, status=filtro.status)
        return self._gerar(exp, filtro)

    def _gerar(self, exp: Exportacao, filtro: Filtro) -> Iterator[str]:
        buffer = io.StringIO()
        escritor = csv.writer(buffer, delimiter=";", lineterminator="\r\n")
        escritor.writerow(exp.colunas)
        yield BOM + _esvaziar(buffer)
        with transacao(self._fabrica) as s:
            resultado = s.execute(exp.consulta(filtro, self._tz).execution_options(yield_per=LOTE))
            for n, linha in enumerate(resultado, 1):
                escritor.writerow([seguro(v) for v in exp.linha(linha, self._tz)])
                if n % LOTE == 0:
                    yield _esvaziar(buffer)
        resto = _esvaziar(buffer)
        if resto:
            yield resto

    def pacote_zip(self, comerciante_id: int) -> bytes:
        """ZIP com todos os CSVs de um comerciante (o volume por comerciante é pequeno: cabe em memória)."""
        filtro = Filtro(comerciante_id=comerciante_id)
        memoria = io.BytesIO()
        with zipfile.ZipFile(memoria, "w", zipfile.ZIP_DEFLATED) as z:
            for nome in PACOTE:
                z.writestr(f"{nome}.csv", "".join(self.csv(nome, filtro)).encode("utf-8"))
        return memoria.getvalue()


def _esvaziar(buffer: io.StringIO) -> str:
    texto = buffer.getvalue()
    buffer.seek(0)
    buffer.truncate()
    return texto


def juntar(pedacos: Iterable[str]) -> str:
    return "".join(pedacos)
