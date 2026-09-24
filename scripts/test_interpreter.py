"""Fase 1 — mostra o que o LLM do Groq entende de frases típicas.

Uso:  python scripts/test_interpreter.py            (frases de exemplo)
      python scripts/test_interpreter.py "vendi 2 quilos de feijão"
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings  # noqa: E402
from app.llm.interpreter import GroqInterpreter  # noqa: E402

FRASES = [
    "comprei 300 kg de arroz, me custou 300 reais",
    "vendi 5 quilos de arroz por 25 reais",
    "chegou 10 caixas de leite, paguei 4 reais cada caixa",
    "vendi duas dúzias de ovo e meio quilo de café",
    "quanto arroz eu tenho?",
    "o que tem no estoque?",
    "desfaz o último aí, falei errado",
    "comprei uns saco de feijão",
    "bom dia, tudo bem?",
    "repus o óleo, 12 unidades a 8 e 50",
]

s = get_settings()
interpreter = GroqInterpreter(s.groq_api_key, s.groq_model)
produtos = ["arroz", "feijão", "óleo de soja", "leite"]

for frase in sys.argv[1:] or FRASES:
    inicio = time.perf_counter()
    intencao = interpreter.interpretar(frase, produtos)
    print(f"[{time.perf_counter() - inicio:.1f}s] {frase!r}\n    -> {intencao.model_dump_json()}\n")
