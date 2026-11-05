"""Interfaces de armazenamento. Implementações: Google Sheets e Postgres (o container escolhe)."""

from typing import Protocol

from app.domain.models import Comerciante, Movimento, ProdutoEstoque


class InventoryRepository(Protocol):
    def carregar_estoque(self) -> dict[str, ProdutoEstoque]: ...
    def carregar_movimentos(self) -> list[Movimento]: ...
    def salvar(self, movimentos: list[Movimento], estoque_alterado: list[ProdutoEstoque]) -> None: ...


class ComercianteRepository(Protocol):
    def obter_ou_criar(self, telefone: str, nome: str | None, agora: str) -> tuple[Comerciante, bool]:
        """Retorna (comerciante, foi_criado_agora)."""
        ...
