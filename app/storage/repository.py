"""Interface de armazenamento. Hoje é Google Sheets; amanhã pode ser Postgres sem mexer no resto."""

from typing import Protocol

from app.domain.models import Movimento, ProdutoEstoque


class InventoryRepository(Protocol):
    def carregar_estoque(self) -> dict[str, ProdutoEstoque]: ...
    def carregar_movimentos(self) -> list[Movimento]: ...
    def salvar(self, movimentos: list[Movimento], estoque_alterado: list[ProdutoEstoque]) -> None: ...
