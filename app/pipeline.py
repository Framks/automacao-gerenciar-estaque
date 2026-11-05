"""Orquestra uma mensagem: cadastro -> áudio -> texto -> contexto -> operações -> banco -> resposta.

Cada mensagem fica registrada (tabela `mensagens`) com status, etapa do erro, intenção e tempo por etapa.
A IA só interpreta; o motor da conversa (app/conversa/atendimento.py) decide o que gravar e o que perguntar.
"""

import logging
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Protocol
from zoneinfo import ZoneInfo

from app.conversa.atendimento import Atendimento
from app.conversa.contexto import LimitesContexto, MensagemHistorico, montar
from app.conversa.loja import Loja
from app.conversa.modelos import Parametros
from app.domain import replies
from app.domain.models import Comerciante
from app.errors import ServicoIndisponivel
from app.fila.controle import Controle
from app.llm.interpreter import PROMPT_VERSAO, Interpreter
from app.observabilidade.cronometro import Cronometro
from app.speech.stt import SpeechToText
from app.speech.tts import TextToSpeech
from app.storage.repository import ComercianteRepository
from app.whatsapp.client import MensagemRecebida, WhatsApp, mascarar

log = logging.getLogger(__name__)

ERRO_GENERICO = "Desculpe, tive um problema para anotar. Pode mandar o áudio de novo daqui a pouco?"
FORMATO = "%Y-%m-%d %H:%M:%S"


class Mensagens(Protocol):
    def registrar_entrada(self, comerciante_id: int | None, wa_msg_id: str, tipo: str, texto: str = "",
                          telefone_mascarado: str = "") -> tuple[int, str]: ...
    def atualizar(self, mensagem_id: int, **campos) -> None: ...
    def registrar_saida(self, comerciante_id: int | None, resposta_a: int | None, tipo: str, texto: str,
                        wa_msg_id: str | None, erro: str = "") -> None: ...
    def historico(self, comerciante_id: int, desde: str, limite: int = 6) -> list[MensagemHistorico]: ...


def _erro_curto(e: BaseException) -> str:
    return f"{type(e).__name__}: {str(e)[:300]}"


class Pipeline:
    def __init__(
        self,
        whatsapp: WhatsApp,
        stt: SpeechToText,
        interpreter: Interpreter,
        tts: TextToSpeech,
        tenants: ComercianteRepository,
        loja: Callable[[Comerciante], Loja],
        mensagens: Mensagens,
        controle: Controle,
        timezone: str = "America/Fortaleza",
        parametros: Callable[[Comerciante], Parametros] = lambda c: Parametros(),
        limites: Callable[[Comerciante], LimitesContexto] = lambda c: LimitesContexto(),
        resposta_em_audio: Callable[[Comerciante], bool] = lambda c: True,
        tts_de: Callable[[Comerciante], TextToSpeech] | None = None,
    ):
        self._wa = whatsapp
        self._stt = stt
        self._interpreter = interpreter
        self._tts = tts
        self._tts_de = tts_de
        self._tenants = tenants
        self._loja = loja
        self._mensagens = mensagens
        self._controle = controle
        self._tz = ZoneInfo(timezone)
        self._parametros = parametros
        self._limites = limites
        self._resposta_em_audio = resposta_em_audio

    def processar(self, msg: MensagemRecebida) -> None:
        """Processa uma mensagem. Lança ServicoIndisponivel quando vale a pena a fila tentar de novo."""
        with self._controle.trava(msg.telefone):
            if self._controle.ja_processada(msg.msg_id):
                log.info("mensagem %s já processada, ignorando", msg.msg_id)
                return
            cron = Cronometro()
            agora = datetime.now(self._tz).strftime(FORMATO)
            with cron.etapa("cadastro"):
                comerciante, novo = self._tenants.obter_ou_criar(msg.telefone, msg.nome, agora)
                mid, status = self._mensagens.registrar_entrada(
                    comerciante.id, msg.msg_id, msg.tipo, (msg.texto or "").strip(), mascarar(msg.telefone))

            if comerciante.status != "ativo":
                log.info("telefone %s com status %s: ignorando", mascarar(msg.telefone), comerciante.status)
                self._mensagens.atualizar(mid, status="ignorada", texto="")
                self._controle.marcar_processada(msg.msg_id)
                return
            if status in ("processada", "erro"):  # o Redis perdeu a marca, mas o banco lembra
                self._controle.marcar_processada(msg.msg_id)
                return

            try:
                resposta = self._processar(msg, comerciante, mid, cron, agora)
            except ServicoIndisponivel as e:
                self._anotar(mid, status="reprocessando", etapa_erro=cron.etapa_erro, erro=_erro_curto(e),
                             tempos_json=cron.tempos)
                raise
            except Exception as e:
                log.exception("erro processando %s na etapa %s", msg.msg_id, cron.etapa_erro)
                self._anotar(mid, status="erro", etapa_erro=cron.etapa_erro or "desconhecida", erro=_erro_curto(e),
                             tempos_json=cron.tempos)
                self._controle.marcar_processada(msg.msg_id)
                self._responder(comerciante, mid, ERRO_GENERICO, cron)
                return

            if novo:
                resposta = f"{replies.boas_vindas(comerciante.nome)} {resposta}"
            self._responder(comerciante, mid, resposta, cron)
            self._anotar(mid, tempos_json=cron.tempos)
            log.info("processado tel=%s tempos=%s total=%.2fs", mascarar(msg.telefone), cron.tempos, cron.total)

    def _processar(self, msg: MensagemRecebida, comerciante: Comerciante, mid: int, cron: Cronometro,
                   agora: str) -> str:
        if msg.tipo == "audio":
            with cron.etapa("download_midia"):
                audio = self._wa.baixar_midia(msg.media_id)
            with cron.etapa("transcricao"):
                texto = self._stt.transcrever(audio)
        else:
            texto = (msg.texto or "").strip()

        loja = self._loja(comerciante)
        limites = self._limites(comerciante)
        with cron.etapa("contexto"):
            estado = loja.carregar_estado(agora)
            desde = (datetime.strptime(agora, FORMATO) - timedelta(hours=limites.janela_horas)).strftime(FORMATO)
            historico = self._mensagens.historico(comerciante.id, desde, limites.max_mensagens)
            contexto = montar(texto, estado, historico, agora, limites)

        with cron.etapa("interpretacao"):
            entendido = self._interpreter.interpretar(texto, contexto)

        with cron.etapa("dominio"):
            saida = Atendimento(self._parametros(comerciante)).processar(
                entendido, estado, texto=texto, msg_id=msg.msg_id, agora=agora)

        with cron.etapa("banco"):
            loja.gravar(saida.plano, mid)
        # marcado logo depois do commit: se o envio falhar e a fila repetir, não grava duas vezes
        self._controle.marcar_processada(msg.msg_id)
        self._anotar(mid, status="processada", transcricao=texto, tempos_json=cron.tempos, intencao_json={
            "prompt": PROMPT_VERSAO, "operacoes": entendido.model_dump(mode="json")["operacoes"],
            "resultado": saida.resumo, "pergunta": saida.pergunta,
        })
        return saida.texto

    def _responder(self, comerciante: Comerciante, mid: int, texto: str, cron: Cronometro) -> None:
        telefone = comerciante.telefone
        if self._resposta_em_audio(comerciante):
            try:
                tts = self._tts_de(comerciante) if self._tts_de else self._tts
                with cron.etapa("tts"):
                    audio = tts.sintetizar(texto)
                with cron.etapa("envio_audio"):
                    wa_id = self._wa.enviar_audio(telefone, audio)
                self._registrar_saida(comerciante, mid, "audio", texto, wa_id)
            except Exception as e:
                log.exception("falha ao gerar/enviar áudio; mandando só o texto")
                self._registrar_saida(comerciante, mid, "audio", texto, None, _erro_curto(e))
        try:
            with cron.etapa("envio_texto"):
                wa_id = self._wa.enviar_texto(telefone, texto)
            self._registrar_saida(comerciante, mid, "text", texto, wa_id)
        except Exception as e:
            log.exception("falha ao enviar texto para %s", mascarar(telefone))
            self._registrar_saida(comerciante, mid, "text", texto, None, _erro_curto(e))

    def _anotar(self, mid: int, **campos) -> None:
        try:
            self._mensagens.atualizar(mid, **campos)
        except Exception:
            log.exception("não consegui atualizar a mensagem %s", mid)

    def _registrar_saida(self, comerciante: Comerciante, mid: int, tipo: str, texto: str, wa_id: str | None,
                         erro: str = "") -> None:
        try:
            self._mensagens.registrar_saida(comerciante.id, mid, tipo, texto, wa_id, erro)
        except Exception:
            log.exception("não consegui registrar a saída da mensagem %s", mid)
