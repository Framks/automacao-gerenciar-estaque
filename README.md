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

## Como subir uma mudança

A `main` é protegida: nada chega à produção sem passar por PR e testes.

1. Crie uma branch e abra um PR. O CI roda os testes, o teste de fumaça e o `md-guard` (recusa arquivos `.md` novos). A Vercel cria um deploy de preview do PR.
2. O preview usa as variáveis de *Preview* da Vercel: banco próprio, prefixo `preview:` no Redis e processamento sem fila (`PROCESSAR_SEM_FILA`). A fila chamaria o worker da produção.
3. Teste o preview sem mexer no webhook da Meta. O script assina o payload como a Meta faria, e a resposta chega no seu WhatsApp:
   ```bash
   python scripts/simular_webhook.py --url https://<preview>.vercel.app --texto "comprei 2 kg de arroz"
   python scripts/simular_webhook.py --url https://<preview>.vercel.app --falar "vendi 1 quilo de arroz"
   ```
   O `--falar` gera um áudio com a voz do bot e o envia como mensagem de voz, passando pela transcrição. O número vem de `TELEFONE_TESTE`, e `VERCEL_PROTECTION_BYPASS` passa pela proteção do preview. As duas ficam no `.env`.
4. Com os checks verdes, faça o merge. O CI roda os testes de novo, faz o deploy de produção pela API da Vercel (o deploy automático da `main` está desligado no `vercel.json`) e confere o `/api/health`, que mostra o commit publicado.

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

Rodar localmente: `uvicorn main:app --reload`. Sem `UPSTASH_*` e `QSTASH_TOKEN`, o app usa Redis em memória e processa a mensagem na hora, sem fila. Para a Meta alcançar o seu PC, use um túnel (ex.: `cloudflared tunnel --url http://localhost:8000`).

## Estrutura

| Caminho | O que faz |
|---|---|
| `main.py` | Rotas HTTP (webhook, worker e health); entrada detectada pela Vercel |
| `app/pipeline.py` | Orquestra o fluxo de uma mensagem |
| `app/domain/` | Regras do estoque, dinheiro (`Decimal`), operações v2, caixa, unidades e textos de resposta (sem rede) |
| `app/conversa/` | Motor da conversa: pendências (o que falta perguntar), confirmação em caso de dúvida e contexto para a IA |
| `app/llm/interpreter.py` | Fala + contexto → operações em JSON (Groq). A IA só interpreta; o código decide e calcula |
| `app/observabilidade/` | Tempo por etapa e etapa do erro de cada mensagem (tabela `mensagens`) |
| `app/db/`, `migrations/` | Postgres (Neon): schema e migrações (Alembic) |
| `app/servicos/` | Casos de uso compartilhados com o admin (exportação CSV) |
| `app/speech/` | Transcrição (Groq Whisper) e voz (edge-tts + conversão OGG/Opus) |
| `app/storage/` | Postgres (estoque, conversa, mensagens) e o Google Sheets antigo (só para a migração) |
| `app/fila/` | QStash (fila), descarte de duplicadas e trava por telefone (Redis) |
| `app/container.py` | Monta as implementações reais; é o único lugar a mudar para trocar de provedor |

## Conversa e precisão

- Cada fala vira uma lista de operações: compra, venda, despesa, retirada, completar, corrigir, consulta, desfazer, confirmar, cancelar ou conversa.
- **Falta um dado obrigatório** (ex.: a quantidade): nada é gravado, e o bot pergunta.
- **Falta um dado desejável** (ex.: o valor da compra, a categoria do gasto): o bot grava e pergunta. A resposta vira um `ajuste_valor` no livro-caixa, sem reescrever o original.
- **Em caso de dúvida**, o bot confirma antes de gravar: confiança baixa, valor sem verbo ("o feijão foi 10 reais"), preço muito fora do custo ou valor alto.
- Toda mensagem fica na tabela `mensagens`, com transcrição, intenção, status, etapa do erro e tempo por etapa.
- **Avaliação do interpretador:** rode `python scripts/avaliar_interpretador.py` antes de mudar o prompt ou o modelo. São 68 casos, e os casos críticos não podem falhar.
- **Limpeza diária:** `python scripts/agendar_limpeza.py` agenda no QStash a expiração das pendências e a retenção das mensagens.

## Planilhas (antigo, antes do Postgres)

Uma planilha só (`SPREADSHEET_ID`), com as abas:

- `usuarios`: `telefone | nome | status | criado_em`.
  - Um número novo é cadastrado sozinho, se estiver em `TELEFONES_PERMITIDOS` (separados por vírgula, com DDI). Vazia, a lista libera qualquer número. Fora dela, a mensagem é ignorada já no webhook, sem resposta.
  - Para bloquear um número, mude `status` para `bloqueado`. A mudança vale em até 1h, por causa do cache.
- Para cada comerciante:
  - `<telefone>_movimentacoes`: livro-caixa, só recebe linhas novas;
  - `<telefone>_estoque`: posição atual.

Uma planilha comporta 10 milhões de células; cada comerciante novo ocupa cerca de 1.600. Isso dá folga para alguns milhares de comerciantes, dependendo do volume de movimentações.
