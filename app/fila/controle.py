"""Descarte de duplicadas e trava por telefone, usando Redis (Upstash)."""

import time
import uuid
from contextlib import contextmanager

from app.errors import ServicoIndisponivel

DOIS_DIAS = 2 * 24 * 3600


class TravaOcupada(ServicoIndisponivel):
    pass


class Controle:
    def __init__(self, redis):
        self._redis = redis

    def primeira_vez(self, msg_id: str) -> bool:
        """True só na primeira vez que o webhook entrega esta mensagem (a Meta pode reenviar)."""
        return bool(self._redis.set(f"wa:recebida:{msg_id}", "1", nx=True, ex=DOIS_DIAS))

    def esquecer(self, msg_id: str) -> None:
        """Desfaz `primeira_vez` (ex.: falhou ao enfileirar e queremos aceitar o reenvio da Meta)."""
        self._redis.delete(f"wa:recebida:{msg_id}")

    def ja_processada(self, msg_id: str) -> bool:
        return self._redis.get(f"wa:processada:{msg_id}") is not None

    def marcar_processada(self, msg_id: str) -> None:
        self._redis.set(f"wa:processada:{msg_id}", "1", ex=DOIS_DIAS)

    @contextmanager
    def trava(self, telefone: str, espera: float = 20, ttl: int = 90):
        """Um processamento por vez para cada telefone (cadastro + estoque)."""
        chave, dono = f"trava:{telefone}", uuid.uuid4().hex
        limite = time.monotonic() + espera
        while not self._redis.set(chave, dono, nx=True, ex=ttl):
            if time.monotonic() > limite:
                raise TravaOcupada(f"trava ocupada para {telefone}")
            time.sleep(0.3)
        try:
            yield
        finally:
            if self._redis.get(chave) == dono:
                self._redis.delete(chave)


class MemoriaRedis:
    """Substituto em memória do Redis, para testes e desenvolvimento local."""

    def __init__(self):
        self._dados: dict[str, tuple[str, float | None]] = {}

    def get(self, key):
        valor = self._dados.get(key)
        if valor is None:
            return None
        if valor[1] is not None and valor[1] < time.monotonic():
            del self._dados[key]
            return None
        return valor[0]

    def set(self, key, value, ex=None, nx=False):
        if nx and self.get(key) is not None:
            return None
        self._dados[key] = (value, time.monotonic() + ex if ex else None)
        return True

    def delete(self, key):
        self._dados.pop(key, None)
