"""Cria (ou atualiza) no QStash o agendamento diário de /api/tarefas/limpeza.

A tarefa expira pendências vencidas e apaga mensagens mais antigas que a retenção.
Uso: python scripts/agendar_limpeza.py [--cron "0 6 * * *"]   (horário em UTC; 06:00 UTC = 03:00 em Fortaleza)
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from qstash import QStash  # noqa: E402

from app.config import get_settings  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cron", default="0 6 * * *")
    args = ap.parse_args()
    s = get_settings()
    destino = s.base_url + "/api/tarefas/limpeza"
    cliente = QStash(s.qstash_token, base_url=s.qstash_url or None)
    existentes = [a for a in cliente.schedule.list() if a.destination == destino]
    for antigo in existentes:
        cliente.schedule.delete(antigo.schedule_id)
    agendamento = cliente.schedule.create(destination=destino, cron=args.cron, retries=2)
    print(f"agendado: {destino} ({args.cron} UTC) id={agendamento}; removidos {len(existentes)} antigos")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
