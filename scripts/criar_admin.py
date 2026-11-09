"""Cria (ou redefine a senha de) um admin do painel. A senha é gerada aqui e mostrada uma vez.

Uso: python scripts/criar_admin.py --email voce@exemplo.com [--papel admin|leitura] [--url URL_DO_BANCO]
No primeiro login, o painel pede para trocar a senha.
"""

import argparse
import secrets
import string
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import func, select  # noqa: E402

from app.admin.auth import gerar_hash  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db.engine import fabrica, transacao  # noqa: E402
from app.db.modelos import AdminDB  # noqa: E402


def senha_aleatoria(tamanho: int = 16) -> str:
    alfabeto = string.ascii_letters + string.digits
    while True:
        senha = "".join(secrets.choice(alfabeto) for _ in range(tamanho))
        if any(c.islower() for c in senha) and any(c.isupper() for c in senha) and any(c.isdigit() for c in senha):
            return senha


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--email", required=True)
    ap.add_argument("--papel", choices=["admin", "leitura"], default="admin")
    ap.add_argument("--url")
    args = ap.parse_args()
    email = args.email.strip().lower()
    senha = senha_aleatoria()
    with transacao(fabrica(args.url or get_settings().banco_url)) as s:
        admin = s.scalar(select(AdminDB).where(func.lower(AdminDB.email) == email))
        if admin is None:
            s.add(AdminDB(email=email, senha_hash=gerar_hash(senha), papel=args.papel, trocar_senha=True))
            acao = "criado"
        else:
            admin.senha_hash, admin.trocar_senha, admin.ativo, admin.papel = gerar_hash(senha), True, True, args.papel
            acao = "senha redefinida"
    print(f"Admin {email} ({args.papel}) {acao}. Senha provisória: {senha}")
    print("Troque no primeiro login.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
