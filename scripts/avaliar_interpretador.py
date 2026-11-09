"""Mede a precisão do interpretador nos casos de tests/avaliacao/casos.jsonl (chama o Groq de verdade).

Uso:
  python scripts/avaliar_interpretador.py                    # prompt v2, modelo do .env
  python scripts/avaliar_interpretador.py --prompt v1        # linha de base (prompt antigo, sem contexto)
  python scripts/avaliar_interpretador.py --repeticoes 3 --modelo openai/gpt-oss-20b --so feijao

Sai com código 1 se algum caso crítico falhar (ou se o acerto ficar abaixo de --meta).
O relatório completo vai para avaliacao_<data>.json (no diretório passado em --saida; padrão: o atual).
"""

import argparse
import json
import statistics
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings  # noqa: E402
from app.conversa.contexto import Contexto  # noqa: E402
from app.errors import ServicoIndisponivel  # noqa: E402
from app.llm.interpreter import GroqInterpreter  # noqa: E402
from tests.avaliacao.comparar import diferencas  # noqa: E402

CATALOGO = [
    {"produto": "arroz", "unidade": "kg", "quantidade": "300", "custo_medio": "1"},
    {"produto": "feijão", "unidade": "kg", "quantidade": "8", "custo_medio": "sem custo"},
    {"produto": "carne", "unidade": "kg", "quantidade": "5", "custo_medio": "sem custo"},
    {"produto": "farinha", "unidade": "kg", "quantidade": "2", "custo_medio": "25"},
    {"produto": "leite", "unidade": "l", "quantidade": "3", "custo_medio": "sem custo"},
    {"produto": "café", "unidade": "kg", "quantidade": "4", "custo_medio": "30"},
    {"produto": "açúcar", "unidade": "saco", "quantidade": "10", "custo_medio": "25"},
    {"produto": "ovo", "unidade": "duzia", "quantidade": "5", "custo_medio": "12"},
    {"produto": "óleo de soja", "unidade": "un", "quantidade": "6", "custo_medio": "8"},
]
CLIENTES = ["João", "Dona Maria", "Seu Zé", "João Pedro"]
MOVIMENTOS = [{"id": "wamid.ARZ-0", "tipo": "entrada", "produto": "arroz", "quantidade": "10", "unidade": "kg",
               "valor_total": "50", "quando": "2026-10-29 09:50:00"}]


def contexto_do_caso(caso: dict) -> Contexto:
    ctx = caso.get("contexto", {})
    return Contexto(agora="2026-10-29 10:00:00", historico=ctx.get("historico", []),
                    pendencias=ctx.get("pendencias", []), produtos=ctx.get("produtos", CATALOGO),
                    ultimos_movimentos=ctx.get("ultimos_movimentos", MOVIMENTOS),
                    clientes_fiado=ctx.get("clientes_fiado", CLIENTES))


def interpretar(g: GroqInterpreter, prompt: str, caso: dict):
    if prompt == "v1":
        return g.interpretar_v1(caso["fala"], [p["produto"] for p in CATALOGO])
    return g.interpretar(caso["fala"], contexto_do_caso(caso))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--prompt", choices=["v1", "v2"], default="v2")
    ap.add_argument("--modelo")
    ap.add_argument("--repeticoes", type=int, default=1)
    ap.add_argument("--so", help="só os casos cujo id contém este texto")
    ap.add_argument("--meta", type=float, default=0.0, help="acerto mínimo (0 a 1) para sair com sucesso")
    ap.add_argument("--saida", default=".")
    ap.add_argument("--pausa", type=float, default=0.3, help="segundos entre chamadas (limite do Groq)")
    args = ap.parse_args()

    s = get_settings()
    modelo = args.modelo or s.groq_model
    g = GroqInterpreter(s.groq_api_key, modelo)
    casos = [json.loads(l) for l in Path("tests/avaliacao/casos.jsonl").read_text(encoding="utf-8").splitlines()]
    if args.so:
        casos = [c for c in casos if args.so in c["id"]]

    resultados, latencias, tokens = [], [], []
    por_tipo, confusao = defaultdict(lambda: [0, 0]), Counter()
    for caso in casos:
        for rep in range(args.repeticoes):
            for tentativa in range(4):
                try:
                    obtido = interpretar(g, args.prompt, caso)
                    break
                except ServicoIndisponivel as e:
                    espera = 5 * (tentativa + 1)
                    print(f"  limite do Groq ({e}); esperando {espera}s")
                    time.sleep(espera)
            else:
                print("Groq indisponível; parando.")
                return 2
            latencias.append(g.ultimo_uso.segundos)
            tokens.append(g.ultimo_uso.tokens_entrada + g.ultimo_uso.tokens_saida)
            erros = diferencas(caso["esperado"], obtido)
            tipo_esperado = caso["esperado"][0]["tipo"]
            tipo_obtido = obtido.operacoes[0].tipo.value if obtido.operacoes else "(nada)"
            por_tipo[tipo_esperado][0] += not erros
            por_tipo[tipo_esperado][1] += 1
            if tipo_esperado != tipo_obtido:
                confusao[f"{tipo_esperado} -> {tipo_obtido}"] += 1
            resultados.append({"id": caso["id"], "critico": caso.get("critico", False), "repeticao": rep,
                               "ok": not erros, "erros": erros, "obtido": obtido.model_dump(mode="json")})
            marca = "ok " if not erros else ("XX " if caso.get("critico") else "-- ")
            print(f"{marca}{caso['id']}" + ("" if not erros else f"  | {erros[0]}"))
            time.sleep(args.pausa)

    total = len(resultados)
    acertos = sum(r["ok"] for r in resultados)
    criticos_falhos = sorted({r["id"] for r in resultados if r["critico"] and not r["ok"]})
    completar_para_venda = confusao.get("completar -> venda", 0)
    lat = sorted(latencias)
    print(f"\nPrompt {args.prompt} | modelo {modelo} | {len(casos)} casos × {args.repeticoes}")
    print(f"Acerto geral: {acertos}/{total} = {acertos / total:.1%}")
    for tipo, (ok, n) in sorted(por_tipo.items()):
        print(f"  {tipo:10} {ok}/{n}")
    if confusao:
        print("Confusões de tipo: " + ", ".join(f"{k} ({v})" for k, v in confusao.most_common()))
    print(f"Latência média {statistics.mean(lat):.2f}s, p95 {lat[int(0.95 * (len(lat) - 1))]:.2f}s | "
          f"tokens médios {statistics.mean(tokens):.0f}")
    print(f"Críticos com falha: {criticos_falhos or 'nenhum'} | completar -> venda: {completar_para_venda}")

    relatorio = Path(args.saida) / f"avaliacao_{args.prompt}_{datetime.now():%Y%m%d_%H%M%S}.json"
    relatorio.write_text(json.dumps({
        "prompt": args.prompt, "modelo": modelo, "acerto": acertos / total, "por_tipo": por_tipo,
        "confusao": confusao, "criticos_falhos": criticos_falhos, "resultados": resultados,
    }, ensure_ascii=False, indent=1, default=list), encoding="utf-8")
    print(f"Relatório: {relatorio}")
    return 1 if criticos_falhos or acertos / total < args.meta else 0


if __name__ == "__main__":
    raise SystemExit(main())
