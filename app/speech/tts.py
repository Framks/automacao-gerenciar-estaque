"""Síntese de voz (text-to-speech).

Usa o edge-tts: vozes neurais pt-BR da Microsoft, grátis e sem conta (não é API oficial).
O edge-tts gera MP3; convertemos para OGG/Opus (formato de mensagem de voz do WhatsApp)
com o ffmpeg do pacote imageio-ffmpeg. Se a conversão falhar, devolvemos o MP3 mesmo.
"""

import asyncio
import logging
import subprocess
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Protocol

import edge_tts

log = logging.getLogger(__name__)


@dataclass
class AudioGerado:
    conteudo: bytes
    mime_type: str  # "audio/ogg" (mensagem de voz) ou "audio/mpeg"
    extensao: str


class TextToSpeech(Protocol):
    def sintetizar(self, texto: str) -> AudioGerado: ...


class EdgeTTS:
    def __init__(self, voz: str = "pt-BR-FranciscaNeural", velocidade: str = "-5%", converter_ogg: bool = True):
        self._voz = voz
        self._velocidade = velocidade
        self._converter_ogg = converter_ogg

    def sintetizar(self, texto: str) -> AudioGerado:
        # roda o asyncio numa thread separada: funciona mesmo se já houver um event loop (FastAPI)
        with ThreadPoolExecutor(max_workers=1) as executor:
            mp3 = executor.submit(asyncio.run, self._gerar_mp3(texto)).result(timeout=60)
        if self._converter_ogg:
            try:
                return AudioGerado(mp3_para_ogg_opus(mp3), "audio/ogg", "ogg")
            except Exception:
                log.exception("falha ao converter para OGG/Opus; enviando MP3")
        return AudioGerado(mp3, "audio/mpeg", "mp3")

    async def _gerar_mp3(self, texto: str) -> bytes:
        comunicador = edge_tts.Communicate(texto, self._voz, rate=self._velocidade)
        partes = []
        async for bloco in comunicador.stream():
            if bloco["type"] == "audio":
                partes.append(bloco["data"])
        if not partes:
            raise RuntimeError("edge-tts não devolveu áudio")
        return b"".join(partes)


def mp3_para_ogg_opus(mp3: bytes) -> bytes:
    import imageio_ffmpeg  # import tardio: só carrega o binário do ffmpeg quando precisa

    processo = subprocess.run(
        [
            imageio_ffmpeg.get_ffmpeg_exe(), "-hide_banner", "-loglevel", "error",
            "-i", "pipe:0",
            "-c:a", "libopus", "-b:a", "32k", "-ac", "1", "-application", "voip",
            "-f", "ogg", "pipe:1",
        ],
        input=mp3,
        capture_output=True,
        check=True,
        timeout=30,
    )
    return processo.stdout
