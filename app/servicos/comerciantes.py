"""Acesso ao bot (allowlist no banco) e gestão de comerciantes, usados pelo webhook e pelo admin.

Um número desconhecido não é atendido: vira um comerciante `pendente` (sem resposta), que o admin aprova.
"""

import logging

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, sessionmaker

from app.db.engine import transacao
from app.db.modelos import ComercianteDB
from app.whatsapp.client import variantes_telefone, wa_id_brasil

log = logging.getLogger(__name__)

CACHE_TTL = 300
PERMITIDO, PENDENTE, BLOQUEADO = "permitido", "pendente", "bloqueado"


def situacao(linha: ComercianteDB) -> str:
    if linha.status == "bloqueado":
        return BLOQUEADO
    return PERMITIDO if linha.permitido and linha.status == "ativo" else PENDENTE


class Acesso:
    def __init__(self, fabrica: sessionmaker[Session], redis, ttl: int = CACHE_TTL):
        self._fabrica = fabrica
        self._redis = redis
        self._ttl = ttl

    def verificar(self, telefone: str, nome: str | None = None) -> str:
        """permitido | pendente | bloqueado. Número novo é cadastrado como pendente."""
        chave = f"acesso:{telefone}"
        try:
            guardado = self._redis.get(chave)
        except Exception:
            guardado = None
        if guardado in (PERMITIDO, PENDENTE, BLOQUEADO):
            return guardado

        with transacao(self._fabrica) as s:
            linha = s.scalar(select(ComercianteDB).where(ComercianteDB.telefone.in_(variantes_telefone(telefone))))
            if linha is None:
                s.execute(insert(ComercianteDB).values(telefone=telefone, nome=nome or "", status="pendente",
                                                       permitido=False)
                          .on_conflict_do_nothing(index_elements=["telefone"]))
                resultado = PENDENTE
                log.info("número novo aguardando aprovação no admin")
            else:
                resultado = situacao(linha)
        try:
            self._redis.set(chave, resultado, ex=self._ttl)
        except Exception:
            pass
        return resultado

    def esquecer(self, telefone: str) -> None:
        """Depois de aprovar/bloquear no admin: a próxima mensagem já vê a mudança."""
        for t in variantes_telefone(telefone):
            for chave in (f"acesso:{t}", f"pg:tenant:{t}"):
                try:
                    self._redis.delete(chave)
                except Exception:
                    pass


def importar_permitidos(fabrica: sessionmaker[Session], lista: str) -> list[str]:
    """Leva a TELEFONES_PERMITIDOS para o banco (permitido=true). Devolve os wa_id gravados."""
    gravados = []
    with transacao(fabrica) as s:
        for bruto in (t.strip().lstrip("+") for t in lista.split(",") if t.strip()):
            linha = s.scalar(select(ComercianteDB).where(ComercianteDB.telefone.in_(variantes_telefone(bruto))))
            if linha is None:
                linha = ComercianteDB(telefone=wa_id_brasil(bruto), nome="", status="ativo", permitido=True)
                s.add(linha)
            else:
                linha.permitido = True
                if linha.status == "pendente":
                    linha.status = "ativo"
            s.flush()
            gravados.append(linha.telefone)
    return gravados
