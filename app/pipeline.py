"""Orquestra o fluxo completo de uma mensagem: cadastro -> áudio -> texto -> intenção -> planilha -> resposta."""

import logging
import time
from collections.abc import Callable
from datetime import datetime
from zoneinfo import ZoneInfo

from app.domain import inventory, replies
from app.domain.models import Acao
from app.errors import ServicoIndisponivel
from app.fila.controle import Controle
from app.llm.interpreter import Interpreter
from app.speech.stt import SpeechToText
from app.speech.tts import TextToSpeech
from app.storage.repository import InventoryRepository
from app.storage.tenants import TenantStore
from app.whatsapp.client import MensagemRecebida, WhatsApp

log = logging.getLogger(__name__)

ERRO_GENERICO = "Desculpe, tive um problema para anotar. Pode mandar o áudio de novo daqui a pouco?"


class Pipeline:
    def __init__(
        self,
        whatsapp: WhatsApp,
        stt: SpeechToText,
        interpreter: Interpreter,
        tts: TextToSpeech,
        tenants: TenantStore,
        repositorio: Callable[[str], InventoryRepository],  # telefone -> repositório do comerciante
        controle: Controle,
        timezone: str = "America/Fortaleza",
    ):
        self._wa = whatsapp
        self._stt = stt
        self._interpreter = interpreter
        self._tts = tts
        self._tenants = tenants
        self._repositorio = repositorio
        self._controle = controle
        self._tz = ZoneInfo(timezone)

    def processar(self, msg: MensagemRecebida) -> None:
        """Processa uma mensagem. Lança ServicoIndisponivel quando vale a pena a fila tentar de novo."""
        with self._controle.trava(msg.telefone):
            if self._controle.ja_processada(msg.msg_id):
                log.info("mensagem %s já processada, ignorando", msg.msg_id)
                return
            try:
                resposta = self._processar(msg)
            except ServicoIndisponivel:
                raise
            except Exception:
                log.exception("erro processando %s", msg.msg_id)
                self._controle.marcar_processada(msg.msg_id)
                self._responder(msg.telefone, ERRO_GENERICO)
                return
            if resposta is not None:
                self._responder(msg.telefone, resposta)

    def _processar(self, msg: MensagemRecebida) -> str | None:
        tempos: dict[str, float] = {}
        inicio = time.perf_counter()

        def marcar(etapa: str) -> None:
            nonlocal inicio
            agora_ = time.perf_counter()
            tempos[etapa] = round(agora_ - inicio, 2)
            inicio = agora_

        agora = datetime.now(self._tz).strftime("%Y-%m-%d %H:%M:%S")
        comerciante, novo = self._tenants.obter_ou_criar(msg.telefone, msg.nome, agora)
        marcar("cadastro")
        if comerciante.bloqueado:
            log.info("telefone bloqueado: %s", msg.telefone)
            self._controle.marcar_processada(msg.msg_id)
            return None

        if msg.tipo == "audio":
            texto = self._stt.transcrever(self._wa.baixar_midia(msg.media_id))
            marcar("transcricao")
        else:
            texto = (msg.texto or "").strip()

        repo = self._repositorio(comerciante.telefone)
        estoque = repo.carregar_estoque()
        intencao = self._interpreter.interpretar(texto, [p.produto for p in estoque.values()])
        marcar("interpretacao")

        if intencao.acao == Acao.DESFAZER:
            resultado = inventory.desfazer(
                repo.carregar_movimentos(), estoque, texto=texto, msg_id=msg.msg_id, agora=agora
            )
        else:
            resultado = inventory.aplicar(intencao, estoque, texto=texto, msg_id=msg.msg_id, agora=agora)

        if resultado.ok and resultado.movimentos:
            repo.salvar(resultado.movimentos, resultado.estoque_alterado)
        # marcado antes de responder: se o envio falhar e a fila repetir, não grava duas vezes
        self._controle.marcar_processada(msg.msg_id)
        marcar("planilha")

        log.info(
            "processado tel=%s acao=%s ok=%s texto=%r tempos=%s",
            msg.telefone, intencao.acao.value, resultado.ok, texto, tempos,
        )
        resposta = replies.montar(resultado)
        return f"{replies.boas_vindas(comerciante.nome)} {resposta}" if novo else resposta

    def _responder(self, telefone: str, texto: str) -> None:
        try:
            self._wa.enviar_audio(telefone, self._tts.sintetizar(texto))
        except Exception:
            log.exception("falha ao gerar/enviar áudio; mandando só o texto")
        try:
            self._wa.enviar_texto(telefone, texto)
        except Exception:
            log.exception("falha ao enviar texto para %s", telefone)
