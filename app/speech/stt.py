"""Transcrição de áudio (speech-to-text)."""

from typing import Protocol

import groq

from app.errors import ServicoIndisponivel


class SpeechToText(Protocol):
    def transcrever(self, audio: bytes, nome_arquivo: str = "audio.ogg") -> str: ...


class GroqWhisperSTT:
    def __init__(self, api_key: str, model: str, client: groq.Groq | None = None):
        self._client = client or groq.Groq(api_key=api_key)
        self._model = model

    def transcrever(self, audio: bytes, nome_arquivo: str = "audio.ogg") -> str:
        try:
            resposta = self._client.audio.transcriptions.create(
                file=(nome_arquivo, audio),
                model=self._model,
                language="pt",
                response_format="json",
                temperature=0,
            )
        except (groq.RateLimitError, groq.APIConnectionError, groq.InternalServerError) as e:
            raise ServicoIndisponivel(f"Groq STT: {e}") from e
        return resposta.text.strip()
