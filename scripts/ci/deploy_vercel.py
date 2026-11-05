"""Cria um deploy de produção na Vercel a partir de um commit do GitHub, espera ficar pronto e confere o health.

Usado pelo CI depois dos testes (o deploy automático da main está desligado no vercel.json).
Só biblioteca padrão: roda no runner sem instalar nada.

Variáveis: VERCEL_TOKEN, VERCEL_ORG_ID, VERCEL_PROJECT_ID. Opcional: PRODUCAO_URL.
"""

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

API = "https://api.vercel.com"


def chamar(metodo: str, caminho: str, corpo: dict | None = None) -> dict:
    sep = "&" if "?" in caminho else "?"
    req = urllib.request.Request(
        f"{API}{caminho}{sep}teamId={os.environ['VERCEL_ORG_ID']}",
        method=metodo,
        data=json.dumps(corpo).encode() if corpo is not None else None,
        headers={"Authorization": f"Bearer {os.environ['VERCEL_TOKEN']}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        print(f"Vercel respondeu HTTP {e.code}: {e.read()[:500]!r}")
        raise


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo-id", required=True)
    ap.add_argument("--ref", default="main")
    ap.add_argument("--sha", required=True)
    ap.add_argument("--timeout", type=int, default=600)
    args = ap.parse_args()

    projeto = os.environ["VERCEL_PROJECT_ID"]
    deploy = chamar("POST", "/v13/deployments", {
        "name": "automacao-gerenciar-estaque",
        "project": projeto,
        "target": "production",
        "gitSource": {"type": "github", "repoId": args.repo_id, "ref": args.ref, "sha": args.sha},
    })
    print(f"deploy {deploy['id']} criado ({deploy.get('url')})")

    limite = time.monotonic() + args.timeout
    while True:
        estado = chamar("GET", f"/v13/deployments/{deploy['id']}")
        situacao = estado.get("readyState") or estado.get("status")
        if situacao == "READY":
            break
        if situacao in ("ERROR", "CANCELED"):
            print(f"deploy terminou com {situacao}: {estado.get('errorMessage')}")
            return 1
        if time.monotonic() > limite:
            print("tempo esgotado esperando o deploy")
            return 1
        time.sleep(5)
    print("deploy pronto")

    base = os.environ.get("PRODUCAO_URL", "https://automacao-gerenciar-estaque.vercel.app")
    for tentativa in range(10):
        try:
            with urllib.request.urlopen(base + "/api/health", timeout=20) as resp:
                saude = json.loads(resp.read())
            print("health:", json.dumps(saude, ensure_ascii=False))
            if saude.get("variaveis_faltando"):
                print("faltam variáveis de ambiente na produção")
                return 1
            if saude.get("banco") == "erro":
                print("o banco não respondeu")
                return 1
            if saude.get("commit") and not args.sha.startswith(saude["commit"]):
                print(f"o health ainda mostra outro commit ({saude['commit']}); tentando de novo")
                time.sleep(5)
                continue
            return 0
        except (urllib.error.URLError, TimeoutError) as e:
            print(f"health falhou (tentativa {tentativa + 1}): {e}")
            time.sleep(5)
    return 1


if __name__ == "__main__":
    sys.exit(main())
