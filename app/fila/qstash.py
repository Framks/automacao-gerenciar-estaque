"""Fila com retry (Upstash QStash): o webhook publica, o QStash chama /api/worker."""

import logging

from qstash import QStash, Receiver

log = logging.getLogger(__name__)


class Fila:
    def __init__(self, token: str, worker_url: str, base_url: str | None = None):
        self._client = QStash(token, base_url=base_url or None)
        self._worker_url = worker_url

    def publicar(self, job: dict) -> None:
        self._client.message.publish_json(url=self._worker_url, body=job, retries=3)


class VerificadorQStash:
    def __init__(self, current_signing_key: str, next_signing_key: str):
        self._receiver = Receiver(current_signing_key=current_signing_key, next_signing_key=next_signing_key)

    def valido(self, corpo: str, assinatura: str | None, url: str) -> bool:
        if not assinatura:
            return False
        try:
            self._receiver.verify(signature=assinatura, body=corpo, url=url)
            return True
        except Exception as e:  # a lib lança erros diferentes para assinatura inválida/expirada
            log.warning("Assinatura do QStash inválida: %s", e)
            return False
