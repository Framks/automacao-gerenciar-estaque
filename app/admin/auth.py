"""Autenticação do painel admin: senha com argon2, JWT curto de acesso e refresh em cookie HttpOnly.

- acesso: JWT de 15 min no cabeçalho Authorization (o front guarda só em memória);
- refresh: JWT de 7 dias em cookie HttpOnly; Secure; SameSite=Strict, restrito a /api/admin/v1/auth;
- logout: o refresh vai para uma lista de revogados no Redis até expirar;
- limite: 5 tentativas de login a cada 15 min, por IP e por e-mail (Redis, não memória: serverless).
"""

import time
import uuid
from dataclasses import dataclass

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

ACESSO_SEGUNDOS = 15 * 60
REFRESH_SEGUNDOS = 7 * 24 * 3600
LIMITE_TENTATIVAS = 5
JANELA_TENTATIVAS = 15 * 60
COOKIE_REFRESH = "admin_refresh"
CAMINHO_COOKIE = "/api/admin/v1/auth"
ALGORITMO = "HS256"

_hasher = PasswordHasher()


class TokenInvalido(Exception):
    pass


@dataclass
class AdminToken:
    id: int
    email: str
    papel: str  # admin | leitura


def gerar_hash(senha: str) -> str:
    return _hasher.hash(senha)


def senha_confere(senha_hash: str, senha: str) -> bool:
    try:
        return _hasher.verify(senha_hash, senha)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def senha_forte(senha: str) -> str | None:
    """Motivo de recusa, ou None se a senha serve."""
    if len(senha) < 12:
        return "a senha precisa ter pelo menos 12 caracteres"
    if senha.lower() == senha or senha.upper() == senha or not any(c.isdigit() for c in senha):
        return "use letras maiúsculas, minúsculas e números"
    return None


class Tokens:
    def __init__(self, segredo: str, redis, relogio=time.time):
        if len(segredo) < 32:
            raise RuntimeError("ADMIN_JWT_SECRET precisa ter pelo menos 32 caracteres")
        self._segredo = segredo
        self._redis = redis
        self._relogio = relogio

    def acesso(self, admin: AdminToken) -> str:
        agora = int(self._relogio())
        return jwt.encode({"sub": str(admin.id), "email": admin.email, "papel": admin.papel, "tipo": "acesso",
                           "iat": agora, "exp": agora + ACESSO_SEGUNDOS}, self._segredo, algorithm=ALGORITMO)

    def refresh(self, admin: AdminToken) -> str:
        agora = int(self._relogio())
        return jwt.encode({"sub": str(admin.id), "email": admin.email, "papel": admin.papel, "tipo": "refresh",
                           "jti": uuid.uuid4().hex, "iat": agora, "exp": agora + REFRESH_SEGUNDOS},
                          self._segredo, algorithm=ALGORITMO)

    def ler(self, token: str, tipo: str) -> tuple[AdminToken, dict]:
        try:
            dados = jwt.decode(token, self._segredo, algorithms=[ALGORITMO],
                               options={"require": ["exp", "sub", "tipo"]}, leeway=0)
        except jwt.PyJWTError as e:
            raise TokenInvalido(str(e)) from e
        if dados.get("tipo") != tipo:
            raise TokenInvalido("tipo de token errado")
        if dados["exp"] < self._relogio():
            raise TokenInvalido("expirado")
        if tipo == "refresh" and self._redis.get(f"admin:revogado:{dados.get('jti')}"):
            raise TokenInvalido("revogado")
        return AdminToken(int(dados["sub"]), dados.get("email", ""), dados.get("papel", "leitura")), dados

    def revogar(self, dados_refresh: dict) -> None:
        restante = max(int(dados_refresh["exp"] - self._relogio()), 1)
        self._redis.set(f"admin:revogado:{dados_refresh['jti']}", "1", ex=restante)


class LimiteTentativas:
    def __init__(self, redis):
        self._redis = redis

    def _chaves(self, ip: str, email: str) -> list[str]:
        return [f"admin:login:ip:{ip}", f"admin:login:email:{email.lower()}"]

    def bloqueado(self, ip: str, email: str) -> bool:
        return any(int(self._redis.get(k) or 0) >= LIMITE_TENTATIVAS for k in self._chaves(ip, email))

    def falhou(self, ip: str, email: str) -> None:
        for k in self._chaves(ip, email):
            if self._redis.incr(k) == 1:
                self._redis.expire(k, JANELA_TENTATIVAS)

    def acertou(self, ip: str, email: str) -> None:
        self._redis.delete(self._chaves(ip, email)[1])
