# Bot de estoque por áudio (WhatsApp → Google Sheets)

O comerciante manda um áudio ("comprei 300 kg de arroz por 300 reais"), o bot atualiza a planilha dele e responde em áudio. O plano completo está em `task.md`.

```
Meta webhook → /api/webhook → QStash → /api/worker → Groq Whisper → Groq LLM → regras (Python) → Google Sheets → edge-tts → WhatsApp
```

## Rodar os testes

Requer Python 3.11 ou mais novo.

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows  (Linux/Mac: source .venv/bin/activate)
pip install -r requirements-dev.txt
pytest
```

## Setup (Fase 0)

Preencha um `.env` na raiz conforme cria cada conta (ele e o `.env.example` ficam fora do Git). A lista completa de variáveis, com os valores padrão, está em `app/config.py`. Em produção, as variáveis ficam na Vercel (*Settings > Environment Variables*).

1. **Meta for Developers:** Criar app > Empresa > adicionar o produto **WhatsApp**. Em *Configuração da API*:
   - anote o token, o **Phone number ID** e (em *Configurações do app > Básico*) o **App secret**;
   - cadastre até 5 números de teste.
   - O token temporário do painel expira em 24h. Para o deploy, gere um token permanente de *Usuário do sistema* no Business Manager.
2. **Groq:** crie a chave em console.groq.com/keys e coloque em `GROQ_API_KEY`.
3. **Google Sheets** (conta de serviço):
   - No Google Cloud, ative a **Google Sheets API** no projeto da conta de serviço.
   - Gere uma chave JSON da conta de serviço e cole o conteúdo, numa linha só e entre aspas simples, em `GOOGLE_SERVICE_ACCOUNT_JSON`.
   - Na **sua** conta Google, crie uma planilha em branco (ex.: "Bot Estoque").
   - Clique em *Compartilhar*, adicione o `client_email` da conta de serviço (`...@....iam.gserviceaccount.com`) como **Editor** e desmarque "Notificar".
   - Copie o ID da planilha (o trecho entre `/d/` e `/edit` na URL) para `SPREADSHEET_ID`.
   - As abas `usuarios`, `<telefone>_estoque` e `<telefone>_movimentacoes` são criadas sozinhas.
   - A conta de serviço **não consegue criar arquivos** (não tem cota no Drive), por isso tudo fica numa planilha só, com abas por comerciante.
4. **Upstash:** crie um banco Redis (copie a REST URL e o token) e pegue as chaves do QStash.
5. **Vercel:** importe o repositório do GitHub, cadastre todas as variáveis do `.env` em *Settings > Environment Variables* e defina `PUBLIC_BASE_URL` com a URL do deploy.
6. **Webhook da Meta:**
   - em *WhatsApp > Configuração*, coloque a URL de callback `https://SEU-APP.vercel.app/api/webhook` e o token de verificação igual a `WHATSAPP_VERIFY_TOKEN`;
   - assine o campo **messages**.

## Validar cada peça (Fase 1)

```bash
python scripts/test_stt.py meu_audio.ogg      # transcrição
python scripts/test_interpreter.py            # frases de exemplo -> JSON
python scripts/test_sheets.py                 # cadastra "teste000" (abas novas), grava compra e venda
python scripts/test_tts.py                    # gera resposta.ogg para ouvir (edge-tts)
```

Rodar localmente: `uvicorn api.index:app --reload`. Sem `UPSTASH_*` e `QSTASH_TOKEN`, o app usa Redis em memória e processa a mensagem na hora, sem fila. Para a Meta alcançar o seu PC, use um túnel (ex.: `cloudflared tunnel --url http://localhost:8000`).

## Estrutura

| Caminho | O que faz |
|---|---|
| `api/index.py` | Rotas HTTP (webhook e worker) |
| `app/pipeline.py` | Orquestra o fluxo de uma mensagem |
| `app/domain/` | Regras do estoque, unidades e textos de resposta (sem rede, 100% testado) |
| `app/llm/interpreter.py` | Fala → JSON (Groq) |
| `app/speech/` | Transcrição (Groq Whisper) e voz (edge-tts + conversão OGG/Opus) |
| `app/storage/` | Google Sheets (conta de serviço) e cadastro automático de comerciantes |
| `app/fila/` | QStash (fila), descarte de duplicadas e trava por telefone (Redis) |
| `app/container.py` | Monta as implementações reais; é o único lugar a mudar para trocar de provedor |

## Planilhas

Uma planilha só (`SPREADSHEET_ID`), com as abas:

- `usuarios`: `telefone | nome | status | criado_em`.
  - Um número novo é cadastrado sozinho.
  - Para bloquear um número, mude `status` para `bloqueado`. A mudança vale em até 1h, por causa do cache.
- Para cada comerciante:
  - `<telefone>_movimentacoes`: livro-caixa, só recebe linhas novas;
  - `<telefone>_estoque`: posição atual.

Uma planilha comporta 10 milhões de células; cada comerciante novo ocupa cerca de 1.600. Isso dá folga para alguns milhares de comerciantes, dependendo do volume de movimentações.
