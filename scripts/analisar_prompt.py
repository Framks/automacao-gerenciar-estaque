"""Quanto texto vai em cada chamada ao Groq (sem chamar o Groq). Mostra cada parte e o efeito dos limites.

Uso: python scripts/analisar_prompt.py [--produtos 40 --mensagens 6 --movimentos 5 --clientes 20 --pendencias 2]
Tokens são estimados por caracteres (~3,6 caracteres por token em português; varia com o texto).
"""

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.conversa.contexto import Contexto  # noqa: E402
from app.llm.interpreter import PROMPT_V2  # noqa: E402

CARACTERES_POR_TOKEN = 3.6


def tok(n: int) -> int:
    return round(n / CARACTERES_POR_TOKEN)


def contexto_exemplo(produtos: int, mensagens: int, movimentos: int, clientes: int, pendencias: int) -> Contexto:
    nomes = ["arroz", "feijão", "açúcar", "café", "óleo de soja", "farinha de trigo", "leite", "macarrão", "sal",
             "ovo", "carne moída", "frango", "sabão em pó", "detergente", "papel higiênico", "biscoito", "manteiga",
             "queijo", "presunto", "refrigerante"]
    return Contexto(
        agora="2026-11-08 10:00:00",
        historico=[{"de": "comerciante" if i % 2 == 0 else "bot",
                    "texto": "vendi 2 quilos de arroz por 14 reais no pix" if i % 2 == 0 else
                    "Anotado! Saiu 2 quilos de arroz por 14 reais. Tinha 20 quilos, agora tem 18 quilos. Recebido no Pix."}
                   for i in range(mensagens)],
        pendencias=[{"id": 100 + i, "tipo": "completar", "resumo": "os 10 quilos de feijão", "faltando": ["valor"],
                     "pergunta": "Quanto você pagou no feijão? Pode falar o preço do quilo, da unidade ou o total."}
                    for i in range(pendencias)],
        produtos=[{"produto": f"{nomes[i % len(nomes)]}{'' if i < len(nomes) else f' {i}'}", "unidade": "kg",
                   "quantidade": "18.5", "custo_medio": "5.25"} for i in range(produtos)],
        ultimos_movimentos=[{"id": f"wamid.HBgMNTU4ODgxMDYxODkxFQIAEhggQ0E{i:03d}-0", "tipo": "saida",
                             "produto": "arroz", "quantidade": "2", "unidade": "kg", "valor_total": "14",
                             "quando": "2026-11-08 09:58:00"} for i in range(movimentos)],
        clientes_fiado=[f"Cliente {i}" for i in range(clientes)],
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--produtos", type=int, default=40)
    ap.add_argument("--mensagens", type=int, default=6)
    ap.add_argument("--movimentos", type=int, default=5)
    ap.add_argument("--clientes", type=int, default=20)
    ap.add_argument("--pendencias", type=int, default=1)
    a = ap.parse_args()

    ctx = contexto_exemplo(a.produtos, a.mensagens, a.movimentos, a.clientes, a.pendencias)
    fala = "vendi 3 quilos de arroz por 18 reais no pix"
    partes = {k: len(json.dumps(v, ensure_ascii=False, separators=(",", ":"))) for k, v in asdict(ctx).items()}
    usuario = len(f"CONTEXTO: {ctx.para_json()}\n\nFALA: {fala}")
    secoes = PROMPT_V2.split("\n\n")
    total = len(PROMPT_V2) + usuario
    print(f"Prompt fixo (system): {len(PROMPT_V2):>6} caracteres ~{tok(len(PROMPT_V2)):>5} tokens")
    for s in secoes:
        titulo = s.strip().splitlines()[0][:60]
        print(f"   {len(s):>6}  {titulo}")
    print(f"Contexto + fala (user): {usuario:>4} caracteres ~{tok(usuario):>5} tokens")
    for k, n in sorted(partes.items(), key=lambda kv: -kv[1]):
        print(f"   {n:>6}  {k}")
    print(f"TOTAL de entrada:       {total:>4} caracteres ~{tok(total):>5} tokens "
          f"(+ a resposta e o raciocínio do modelo)")
    print(f"Mensagens por minuto no plano gratuito (8.000 tokens/min, ~300 de saída): "
          f"{8000 / (tok(total) + 300):.1f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
