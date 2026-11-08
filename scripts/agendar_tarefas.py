"""Cria (ou atualiza) no QStash os agendamentos das tarefas do bot. Idempotente: troca os antigos da mesma URL.

- /api/tarefas/limpeza     : todo dia (expira pendências vencidas, apaga mensagens além da retenção)
- /api/tarefas/fechamento  : a cada 15 min (manda o fechamento do caixa de quem está no horário)

Uso: python scripts/agendar_tarefas.py [--limpeza "0 6 * * *"] [--fechamento "*/15 * * * *"] [--so limpeza|fechamento]
Horários em UTC (06:00 UTC = 03:00 em Fortaleza). O fechamento usa o fuso de cada comerciante, não o do cron.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from qstash import QStash  # noqa: E402

from app.config import get_settings  # noqa: E402


def agendar(cliente: QStash, destino: str, cron: str) -> str:
    existentes = [a for a in cliente.schedule.list() if a.destination == destino]
    for antigo in existentes:
        cliente.schedule.delete(antigo.schedule_id)
    agendamento = cliente.schedule.create(destination=destino, cron=cron, retries=2)
    return f"agendado: {destino} ({cron} UTC) id={agendamento}; removidos {len(existentes)} antigos"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limpeza", default="0 6 * * *")
    ap.add_argument("--fechamento", default="*/15 * * * *")
    ap.add_argument("--so", choices=["limpeza", "fechamento"])
    args = ap.parse_args()
    s = get_settings()
    if not s.base_url or not s.qstash_token:
        print("faltam PUBLIC_BASE_URL (ou VERCEL_PROJECT_PRODUCTION_URL) e QSTASH_TOKEN")
        return 1
    cliente = QStash(s.qstash_token, base_url=s.qstash_url or None)
    tarefas = {"limpeza": args.limpeza, "fechamento": args.fechamento}
    for nome, cron in tarefas.items():
        if args.so in (None, nome):
            print(agendar(cliente, f"{s.base_url}/api/tarefas/{nome}", cron))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
