"""Fase 1 — gera o áudio de resposta com o edge-tts (convertido para OGG/Opus). Abra o arquivo e escute.

Uso:  python scripts/test_tts.py ["texto"] [voz]   -> gera resposta.ogg (ou resposta.mp3 se a conversão falhar)
Vozes pt-BR: pt-BR-FranciscaNeural, pt-BR-AntonioNeural, pt-BR-ThalitaMultilingualNeural
Listar todas:  edge-tts --list-voices | findstr pt-BR
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings  # noqa: E402
from app.speech.tts import EdgeTTS  # noqa: E402

texto = sys.argv[1] if len(sys.argv) > 1 else (
    "Anotado! Entrou 300 quilos de arroz por 300 reais. Tinha 3 quilos, agora tem 303 quilos."
)
voz = sys.argv[2] if len(sys.argv) > 2 else get_settings().tts_voice

inicio = time.perf_counter()
audio = EdgeTTS(voz).sintetizar(texto)
arquivo = Path(f"resposta.{audio.extensao}")
arquivo.write_bytes(audio.conteudo)
print(f"[{time.perf_counter() - inicio:.1f}s] {arquivo} gerado ({len(audio.conteudo)} bytes, {audio.mime_type}, voz {voz})")
