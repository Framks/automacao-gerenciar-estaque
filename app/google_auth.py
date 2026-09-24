"""Acesso ao Google Sheets com a conta de serviço (GOOGLE_SERVICE_ACCOUNT_JSON).

A conta de serviço não tem cota no Drive, então não cria arquivos: ela edita uma planilha que
você criou e compartilhou com o client_email dela (como Editor). Criar abas dentro dela funciona.
"""

import json

from google.oauth2 import service_account
from googleapiclient.discovery import build

from app.config import Settings

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]


def servico_sheets(settings: Settings):
    info = json.loads(settings.google_service_account_json)
    creds = service_account.Credentials.from_service_account_info(info, scopes=SCOPES)
    return build("sheets", "v4", credentials=creds, cache_discovery=False)
