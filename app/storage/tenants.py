"""Comerciantes (tenants): cadastro na aba `usuarios`. Cadastra automaticamente quem ainda não existe.

Cada comerciante ganha duas abas na planilha compartilhada: <telefone>_estoque e <telefone>_movimentacoes.
Deve ser chamado com a trava do telefone já adquirida (o worker faz isso), para que dois áudios
simultâneos de um número novo não o cadastrem duas vezes.
"""

import json
from dataclasses import asdict, dataclass
from typing import Protocol

from app.storage.sheets_repo import (
    ABA_USUARIOS,
    CABECALHO_USUARIOS,
    abas_do_comerciante,
    executar,
    garantir_abas,
    intervalo,
)

CACHE_TTL = 3600


@dataclass
class Comerciante:
    telefone: str
    nome: str
    status: str = "ativo"
    criado_em: str = ""

    @property
    def bloqueado(self) -> bool:
        return self.status == "bloqueado"


class Cache(Protocol):
    def get(self, key: str) -> str | None: ...
    def set(self, key: str, value: str, ex: int | None = None): ...


class TenantStore:
    def __init__(self, sheets, spreadsheet_id: str, cache: Cache):
        self._sheets = sheets
        self._id = spreadsheet_id
        self._cache = cache
        self._usuarios_ok = False

    def obter_ou_criar(self, telefone: str, nome: str | None, agora: str) -> tuple[Comerciante, bool]:
        """Retorna (comerciante, foi_criado_agora)."""
        bruto = self._cache.get(f"tenant:{telefone}")
        if bruto:
            return Comerciante(**json.loads(bruto)), False

        comerciante = self._buscar(telefone)
        novo = comerciante is None
        if novo:
            # abas primeiro: se falhar no meio, a nova tentativa completa sem duplicar nada
            garantir_abas(self._sheets, self._id, abas_do_comerciante(telefone))
            comerciante = Comerciante(telefone, nome or "", "ativo", agora)
            executar(self._sheets.spreadsheets().values().append(
                spreadsheetId=self._id,
                range=intervalo(ABA_USUARIOS, "A1"),
                valueInputOption="RAW",
                insertDataOption="INSERT_ROWS",
                body={"values": [[telefone, comerciante.nome, "ativo", agora]]},
            ))

        self._cache.set(f"tenant:{telefone}", json.dumps(asdict(comerciante)), ex=CACHE_TTL)
        return comerciante, novo

    def _buscar(self, telefone: str) -> Comerciante | None:
        if not self._usuarios_ok:  # na primeira vez, cria a aba usuarios se a planilha estiver vazia
            garantir_abas(self._sheets, self._id, {ABA_USUARIOS: CABECALHO_USUARIOS})
            self._usuarios_ok = True
        resposta = executar(self._sheets.spreadsheets().values().get(
            spreadsheetId=self._id, range=intervalo(ABA_USUARIOS, "A2:D"), valueRenderOption="UNFORMATTED_VALUE"
        ))
        for linha in resposta.get("values", []):
            linha = [str(v) for v in linha] + [""] * (4 - len(linha))
            if linha[0] == telefone:
                return Comerciante(linha[0], linha[1], linha[2] or "ativo", linha[3])
        return None
