"""Fechamento do caixa automático (F6-04): no horário que o comerciante escolheu, o bot manda o relatório do dia
(e, no dia 1, o do mês anterior).

- `agendar()`: chamado a cada 15 min pelo QStash. Escolhe quem deve receber agora e publica uma tarefa por
  comerciante (fan-out), para um comerciante lento não estourar o tempo da função dos outros.
- `enviar()`: monta e manda o fechamento de um comerciante. A tabela `fechamentos` (UNIQUE comerciante+tipo+dia)
  garante um envio só, mesmo com o QStash repetindo a chamada.
- Janela de 24h do WhatsApp: dentro dela vai o relatório completo; fora, só um template aprovado na Meta (com o
  botão "Ver detalhes"); sem template, o envio fica `sem_janela` e o bot avisa no próximo contato.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, sessionmaker

from app.db.engine import transacao
from app.db.modelos import ComercianteDB, FechamentoDB, MensagemDB
from app.domain import relatorios as rel
from app.domain.periodos import NOMES_MES, mes_anterior, resolver_periodo
from app.pipeline import PREFIXO_BOTAO_CAIXA
from app.whatsapp.client import FORA_DA_JANELA, ErroWhatsApp, mascarar

log = logging.getLogger(__name__)

JANELA_ENVIO = timedelta(hours=2)  # se o agendador ficou fora do ar, ainda manda até 2h depois do horário
JANELA_WHATSAPP = timedelta(hours=24) - timedelta(minutes=5)  # margem para não errar por segundos
TRAVA_ENVIANDO = timedelta(minutes=10)  # "enviando" mais velho que isso é tentativa que morreu: pode refazer
HORARIO_MENSAL_PADRAO = "08:00"
FINAIS = ("enviado", "sem_janela", "vazio")


@dataclass(frozen=True)
class Preferencia:
    comerciante_id: int
    fuso: str
    horario: str  # "" = diário desligado
    mensal: bool


@dataclass(frozen=True)
class Devido:
    comerciante_id: int
    tipo: str  # diario | mensal
    referencia: date  # o dia (diário) ou o dia 1 do mês relatado (mensal)


def _hora(hhmm: str) -> time:
    h, m = hhmm.split(":")
    return time(int(h), int(m))


def devidos(agora: datetime, preferencias: list[Preferencia], ja_feitos: set[tuple[int, str, date]],
            janela: timedelta = JANELA_ENVIO) -> list[Devido]:
    """Quem deve receber agora. Função pura (testada com relógio falso)."""
    lista = []
    for p in preferencias:
        tz = ZoneInfo(p.fuso)
        local = agora.astimezone(tz)
        if p.horario:
            alvo = datetime.combine(local.date(), _hora(p.horario), tz)
            if alvo <= local < alvo + janela:
                lista.append(Devido(p.comerciante_id, "diario", local.date()))
        if p.mensal and local.day == 1:
            alvo = datetime.combine(local.date(), _hora(p.horario or HORARIO_MENSAL_PADRAO), tz)
            if alvo <= local < alvo + janela:
                lista.append(Devido(p.comerciante_id, "mensal", mes_anterior(local.date()).inicio))
    return [d for d in lista if (d.comerciante_id, d.tipo, d.referencia) not in ja_feitos]


def dentro_da_janela(ultima_entrada: datetime | None, agora: datetime) -> bool:
    return ultima_entrada is not None and agora - ultima_entrada < JANELA_WHATSAPP


class Fechamento:
    def __init__(self, fabrica: sessionmaker[Session], configuracoes, whatsapp, mensagens,
                 relatorios: Callable[[int], object], fila=None, fuso_padrao: str = "America/Fortaleza",
                 relogio: Callable[[], datetime] = lambda: datetime.now(timezone.utc)):
        self._fabrica = fabrica
        self._cfg = configuracoes
        self._wa = whatsapp
        self._mensagens = mensagens
        self._relatorios = relatorios
        self._fila = fila
        self._fuso_padrao = fuso_padrao
        self._relogio = relogio

    # --- agendamento (a cada 15 min) --------------------------------------------------------------------------

    def agendar(self) -> dict:
        if not self._cfg.ligada("fechamento_automatico"):
            return {"desligado": True, "devidos": 0}
        agora = self._relogio()
        with transacao(self._fabrica) as s:
            ids = list(s.scalars(select(ComercianteDB.id).where(ComercianteDB.status == "ativo")))
            ja = {(c, t, r) for c, t, r, st in s.execute(
                select(FechamentoDB.comerciante_id, FechamentoDB.tipo, FechamentoDB.referencia, FechamentoDB.status)
                .where(FechamentoDB.referencia >= (agora - timedelta(days=40)).date())) if st in FINAIS}
        prefs = []
        for cid in ids:
            if not self._cfg.ligada("fechamento_automatico", cid):
                continue
            horario = self._cfg.get("fechamento_diario_horario", cid)
            mensal = bool(self._cfg.get("fechamento_mensal", cid))
            if horario or mensal:
                prefs.append(Preferencia(cid, self._fuso(cid), horario, mensal))
        lista = devidos(agora, prefs, ja)
        resultados = []
        for d in lista:
            tarefa = {"comerciante_id": d.comerciante_id, "tipo": d.tipo, "referencia": d.referencia.isoformat()}
            if self._fila is not None:
                self._fila.publicar(tarefa, deduplicacao=f"fechamento-{d.comerciante_id}-{d.tipo}-{d.referencia}")
                resultados.append("publicado")
            else:  # preview/desenvolvimento: sem fila, manda na hora
                try:
                    resultados.append(self.enviar(d.comerciante_id, d.tipo, d.referencia))
                except Exception:
                    log.exception("fechamento do comerciante %s falhou", d.comerciante_id)
                    resultados.append("erro")
        return {"devidos": len(lista), "resultados": resultados}

    def _fuso(self, cid: int) -> str:
        try:
            return self._cfg.get("fuso_horario", cid)
        except Exception:
            return self._fuso_padrao

    # --- envio de um comerciante ---------------------------------------------------------------------------------

    def _reservar(self, cid: int, tipo: str, referencia: date) -> int | None:
        """Marca "enviando". None se já foi feito (ou se outra tentativa está em andamento)."""
        agora = self._relogio()
        with transacao(self._fabrica) as s:
            novo = s.execute(insert(FechamentoDB).values(comerciante_id=cid, tipo=tipo, referencia=referencia)
                             .on_conflict_do_nothing(constraint="uq_fechamentos").returning(FechamentoDB.id)).scalar()
            if novo is not None:
                return novo
            linha = s.scalar(select(FechamentoDB).where(FechamentoDB.comerciante_id == cid, FechamentoDB.tipo == tipo,
                                                        FechamentoDB.referencia == referencia).with_for_update())
            if linha.status in FINAIS or (linha.status == "enviando" and agora - linha.atualizado_em < TRAVA_ENVIANDO):
                return None
            linha.status, linha.tentativas, linha.atualizado_em = "enviando", linha.tentativas + 1, agora
            return linha.id

    def _concluir(self, fid: int, status: str, canal: str = "", wa_msg_id: str = "", erro: str = "") -> None:
        with transacao(self._fabrica) as s:
            s.execute(update(FechamentoDB).where(FechamentoDB.id == fid).values(
                status=status, canal=canal, wa_msg_id=wa_msg_id or "", erro=erro[:500], atualizado_em=func.now()))

    def enviar(self, cid: int, tipo: str, referencia: date | str) -> str:
        if isinstance(referencia, str):
            referencia = date.fromisoformat(referencia)
        fid = self._reservar(cid, tipo, referencia)
        if fid is None:
            return "ja_feito"
        try:
            return self._enviar(fid, cid, tipo, referencia)
        except Exception as e:
            self._concluir(fid, "erro", erro=f"{type(e).__name__}: {e}")
            raise

    def _enviar(self, fid: int, cid: int, tipo: str, referencia: date) -> str:
        with transacao(self._fabrica) as s:
            comerciante = s.get(ComercianteDB, cid)
            telefone, status = comerciante.telefone, comerciante.status
            ultima = s.scalar(select(func.max(MensagemDB.criado_em)).where(
                MensagemDB.comerciante_id == cid, MensagemDB.direcao == "entrada", MensagemDB.status != "ignorada"))
        if status != "ativo":
            self._concluir(fid, "vazio", erro=f"comerciante {status}")
            return "vazio"

        hoje = self._relogio().astimezone(ZoneInfo(self._fuso(cid))).date()
        if tipo == "mensal":
            periodo_falado, formato = referencia.strftime("%Y-%m"), "por_dia"
        else:
            periodo_falado, formato = "data:" + referencia.isoformat(), self._cfg.get("fechamento_formato", cid)
        servico = self._relatorios(cid)
        r = servico.calcular(resolver_periodo(periodo_falado, hoje))
        if r.vazio and tipo == "diario" and not self._cfg.get("fechamento_dia_vazio", cid):
            self._concluir(fid, "vazio")
            return "vazio"

        texto = self._texto(r, tipo, formato)
        if dentro_da_janela(ultima, self._relogio()):
            try:
                wa_id = self._wa.enviar_texto(telefone, texto)
                self._mensagens.registrar_saida(cid, None, "text", texto, wa_id)
                if tipo == "mensal" and self._cfg.get("fechamento_mensal_grafico", cid) \
                        and len(r.dias_com_movimento) >= 3:
                    self._mandar_grafico(cid, telefone, r)
                self._concluir(fid, "enviado", "texto", wa_id)
                return "enviado"
            except ErroWhatsApp as e:
                if e.codigo != FORA_DA_JANELA:
                    raise
                log.info("fechamento de %s caiu fora da janela; tentando template", mascarar(telefone))

        template = self._cfg.get("fechamento_template")
        if not template:
            self._concluir(fid, "sem_janela")
            return "sem_janela"
        payload = f"{PREFIXO_BOTAO_CAIXA}{periodo_falado}|{formato}"
        wa_id = self._wa.enviar_template(telefone, template, self._cfg.get("fechamento_template_idioma"),
                                         rel.texto_curto_template(r), payload)
        self._mensagens.registrar_saida(cid, None, "template", f"[template {template}] " + " | ".join(
            rel.texto_curto_template(r)), wa_id)
        self._concluir(fid, "enviado", "template", wa_id)
        return "enviado"

    @staticmethod
    def _texto(r: rel.RelatorioCaixa, tipo: str, formato: str) -> str:
        if tipo == "mensal":
            return "Fechamento do mês. " + rel.texto_por_dia(r)
        texto = rel.texto_resumo(r, "Fechamento do caixa")
        if formato == "itens" and not r.vazio:
            texto += "\n\n" + rel.texto_itens(r, "venda")
        return texto

    def _mandar_grafico(self, cid: int, telefone: str, r: rel.RelatorioCaixa) -> None:
        from app.servicos.graficos import desenhar, legenda

        try:
            wa_id = self._wa.enviar_imagem(telefone, desenhar(r), "image/png", legenda(r))
            self._mensagens.registrar_saida(cid, None, "image", legenda(r), wa_id)
        except Exception:
            log.exception("não consegui mandar o gráfico do fechamento mensal")

    # --- aviso no próximo contato --------------------------------------------------------------------------

    def aviso_pendente(self, cid: int) -> str:
        """Se um fechamento não saiu por causa da janela de 24h, avisa (uma vez) na próxima conversa."""
        desde = self._relogio() - timedelta(days=3)
        with transacao(self._fabrica) as s:
            linha = s.scalar(select(FechamentoDB).where(
                FechamentoDB.comerciante_id == cid, FechamentoDB.status == "sem_janela",
                FechamentoDB.avisado.is_(False), FechamentoDB.criado_em >= desde)
                .order_by(FechamentoDB.referencia.desc()).limit(1))
            if linha is None:
                return ""
            s.execute(update(FechamentoDB).where(FechamentoDB.comerciante_id == cid,
                                                 FechamentoDB.status == "sem_janela").values(avisado=True))
            ref = linha.referencia
        if linha.tipo == "mensal":
            pedido = f"como foi o caixa de {NOMES_MES[ref.month - 1]}"
        else:
            pedido = f"como foi o caixa do dia {ref:%d/%m}"
        return (f"O fechamento de {ref:%d/%m} não foi enviado: o WhatsApp só deixa eu mandar mensagem até 24 horas "
                f"depois da sua última. Para ver, diga: {pedido}.")
