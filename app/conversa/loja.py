"""Interface de armazenamento da conversa: carrega o estado do comerciante e grava o plano de uma mensagem."""

from typing import Protocol

from app.conversa.modelos import EstadoConversa, Plano


class Loja(Protocol):
    def carregar_estado(self, agora: str) -> EstadoConversa:
        """Estoque, pendências abertas (não vencidas), movimentos recentes e lançamentos do mês."""
        ...

    def gravar(self, plano: Plano, mensagem_id: int | None = None) -> None:
        """Tudo numa transação só. Idempotente se a fila repetir a mesma mensagem."""
        ...
