"""Tempo por etapa e a etapa onde deu erro. Os nomes são os mesmos dos spans planejados (OpenTelemetry, F4-01):
cadastro, download_midia, transcricao, contexto, interpretacao, dominio, banco, tts, envio_audio, envio_texto.
"""

import time
from collections.abc import Iterator
from contextlib import contextmanager


class Cronometro:
    def __init__(self, relogio=time.perf_counter):
        self._relogio = relogio
        self.tempos: dict[str, float] = {}
        self.etapa_erro = ""
        self.etapa_atual = ""

    @contextmanager
    def etapa(self, nome: str) -> Iterator[None]:
        inicio = self._relogio()
        self.etapa_atual = nome
        try:
            yield
        except BaseException:
            self.etapa_erro = self.etapa_erro or nome
            raise
        finally:
            self.tempos[nome] = round(self.tempos.get(nome, 0) + self._relogio() - inicio, 3)
            self.etapa_atual = ""

    @property
    def total(self) -> float:
        return round(sum(self.tempos.values()), 3)
