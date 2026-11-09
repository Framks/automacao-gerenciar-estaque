"""Dependências do FastAPI para a API admin (sobrescrevíveis nos testes com app.dependency_overrides)."""

from collections.abc import Iterator

from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy.orm import Session

from app.admin.auth import AdminToken, LimiteTentativas, TokenInvalido, Tokens
from app.db.engine import transacao


def fabrica():
    from app import container

    return container._fabrica()


def redis():
    from app import container

    return container.redis()


def configuracoes():
    from app import container

    return container.configuracoes()


def acesso():
    from app import container

    return container.acesso()


def tokens(r=Depends(redis)) -> Tokens:
    from app.config import get_settings

    segredo = get_settings().admin_jwt_secret
    if not segredo:
        raise HTTPException(503, "painel admin não configurado (ADMIN_JWT_SECRET)")
    return Tokens(segredo, r)


def limite(r=Depends(redis)) -> LimiteTentativas:
    return LimiteTentativas(r)


def sessao(f=Depends(fabrica)) -> Iterator[Session]:
    """Uma transação por requisição (commit no fim, rollback em erro)."""
    with transacao(f) as s:
        yield s


def admin_atual(authorization: str | None = Header(default=None), t: Tokens = Depends(tokens)) -> AdminToken:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "faça login", headers={"WWW-Authenticate": "Bearer"})
    try:
        admin, _ = t.ler(authorization.split(" ", 1)[1].strip(), "acesso")
    except TokenInvalido:
        raise HTTPException(401, "sessão expirada ou inválida", headers={"WWW-Authenticate": "Bearer"}) from None
    return admin


def pode_escrever(admin: AdminToken = Depends(admin_atual)) -> AdminToken:
    if admin.papel != "admin":
        raise HTTPException(403, "seu papel é só de leitura")
    return admin


def ip_do_cliente(request: Request) -> str:
    encaminhado = request.headers.get("x-forwarded-for", "")
    return encaminhado.split(",")[0].strip() or (request.client.host if request.client else "?")
