"""Escreve admin/openapi.json a partir do app (fonte do cliente tipado do front: npm run gerar-api)."""

import json
import sys
from pathlib import Path

raiz = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(raiz))

import main  # noqa: E402

esquema = main.app.openapi()
esquema["paths"] = {k: v for k, v in esquema["paths"].items() if k.startswith("/api/admin/v1")}
(raiz / "admin" / "openapi.json").write_text(json.dumps(esquema, ensure_ascii=False, indent=1) + "\n",
                                              encoding="utf-8")
print(f"{len(esquema['paths'])} caminhos em admin/openapi.json")
