"""Fase 1 — transcreve um áudio com o Groq Whisper.

Uso:  python scripts/test_stt.py caminho/do/audio.ogg
(Dica: no WhatsApp Web, baixe um áudio seu; ou grave um .ogg/.mp3 qualquer.)
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings  # noqa: E402
from app.speech.stt import GroqWhisperSTT  # noqa: E402

s = get_settings()
arquivo = Path(sys.argv[1])
inicio = time.perf_counter()
texto = GroqWhisperSTT(s.groq_api_key, s.groq_stt_model).transcrever(arquivo.read_bytes(), arquivo.name)
print(f"[{time.perf_counter() - inicio:.1f}s] {texto}")
