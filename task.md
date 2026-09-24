# Criar automação para facilitar a entrada e saida de caixa. V1

## Objetivo

queremos fazer uma automação que utiliza IA para facilitar o gerenciamento de entradas e saidas de um mini estoque que vai estar dentro de uma planilha no google sheets.

## Quem vai ser os usuarios

pequenos comerciantes do interior que só conseguem usar o audio e que tem dificuldade com tecnologia. 

## Fluxo da ação: 

No whatsapp quero ter um bot que vai receber meus audios, a principio quero coisas simples do tipo

- passo 1 Mando um audio contendo isso -> "comprei 300 kg de arroz, me custou 300 reais."
- passo 2 a automação transcreve o audio em texto 
- passo 3 o texto retornado é a entrada para que a ia faça a atualização na tabela ou o melhor caminho para manter os registros.
- passo 4 é pego o estado resultante por exemplo, "na linha do arroz tinha 3 kg e agora tem 303 kg."
- passo 5 a ia gera um texto que vai ser transformado em audio e retornado para o cliente. 

## Etapa que quero validar

se isso é possivel quero que vc faça o plano em uma sessão nesse arquivo. e o passo a passo para que a gente implemente tudo. 

## pre requesitos

o maximo de coisas gratuitas, para validar se é possivel, ex.: automação vai rodar em um container na versel ou coisas do tipo. 

## regra

- deve ser um bot no whatsapp que mais de uma pessoa pode armazenar suas compras e vendas.
- toda ação deve ser pensada em possibilidade de crescimento do projeto.

---

## Plano de implementação (V1)


### Contexto
Pequenos comerciantes com dificuldade com tecnologia precisam registrar compras e vendas só falando. Eles mandam um áudio no WhatsApp ("comprei 300 kg de arroz, me custou 300 reais"). O bot transcreve o áudio, interpreta o que foi dito, atualiza a planilha do comerciante e responde **em áudio** com o estado novo ("o arroz tinha 3 kg, agora tem 303 kg"). O bot precisa atender vários comerciantes, usar o máximo de serviços gratuitos e permitir que o projeto cresça.

**Decisões já tomadas com você:**
- Backend em **Python**.
- **Dados isolados por comerciante**: uma planilha compartilhada com a conta de serviço, e **abas próprias para cada comerciante** (a conta de serviço não consegue criar arquivos no Drive).
- IA pelo **Groq**: Whisper para transcrever e um LLM para interpretar.
- **Cadastro automático**: se o número não estiver na aba `usuarios`, o bot cria o registro e as abas do comerciante.

**Veredito de viabilidade: é possível, e dá para fazer de graça na fase de validação.** Limites que precisam ser conhecidos:
- O número de teste da API oficial do WhatsApp (Meta) só manda mensagens para **até 5 números cadastrados**. Isso basta para validar. Para produção, é preciso verificar a empresa e ter um número próprio (que não pode estar em uso no app do WhatsApp).
- Na cobrança da Meta, **responder dentro da janela de 24h** depois de o cliente mandar mensagem **não custa nada**. Esse é exatamente o nosso fluxo.
- O plano Hobby da Vercel é gratuito, mas **só para uso não comercial**. Quando virar negócio, migrar para o Pro, Cloud Run ou Render. O código não muda.
- A camada gratuita do Groq tem limites por minuto e por dia. Para um piloto, sobra.

---

### Arquitetura

```
WhatsApp (comerciante)
   │ áudio
   ▼
Meta WhatsApp Cloud API ──webhook──► Vercel /api/webhook  (valida, descarta duplicadas, enfileira, responde 200 na hora)
                                          │ publica job
                                          ▼
                                    Upstash QStash (fila com retry)
                                          │
                                          ▼
                                    Vercel /api/worker
                                     0. telefone está na aba usuarios? se não → cria abas + registro
                                     1. baixa o áudio (Graph API)
                                     2. STT: Groq Whisper (pt)
                                     3. LLM Groq: texto → JSON estruturado (intenção + itens)
                                     4. regras de negócio em Python (determinísticas)
                                        → grava nas abas do comerciante
                                     5. monta a resposta → edge-tts (MP3 → OGG/Opus) → envia o áudio + o texto
```

#### Stack gratuita
| Peça | Serviço | Por quê |
|---|---|---|
| WhatsApp | **Meta WhatsApp Cloud API** (oficial) | É grátis para responder e funciona por webhook, então roda em serverless. Não tem risco de banimento. Serve para crescer. |
| Hospedagem | **Vercel Hobby**, Python (FastAPI) | Grátis, deploy pelo GitHub, HTTPS pronto para o webhook. |
| Fila, dedupe e trava | **Upstash QStash + Redis** | O webhook precisa responder 200 rápido, senão a Meta reenvia a mensagem. A fila dá retry. O Redis descarta mensagens repetidas (msg_id), mantém uma trava por telefone e guarda o cache de telefone → planilha. |
| Transcrição | **Groq Whisper `whisper-large-v3-turbo`**, `language="pt"` | Grátis, muito rápido, bom em português. Aceita o .ogg do WhatsApp direto. |
| Interpretação | **LLM no Groq** (ex.: `llama-3.3-70b-versatile`) em modo JSON, validado com Pydantic | Usa a mesma conta e chave do Whisper. O modelo fica configurável por variável de ambiente (`GROQ_MODEL`). |
| Voz de resposta | **edge-tts** (vozes neurais pt-BR da Microsoft, ex.: `pt-BR-FranciscaNeural`) + conversão para OGG/Opus com `imageio-ffmpeg` | Grátis, sem conta e sem chave. Não é API oficial e pode mudar sem aviso; para produção, a troca pelo Azure Speech (mesmas vozes, oficial) afeta só `app/speech/tts.py`. Se a conversão falhar, envia MP3. |
| Dados | **Google Sheets API** com **conta de serviço** (chave JSON), numa planilha compartilhada com ela | Grátis. A conta de serviço não tem cota no Drive e **não cria arquivos**, mas edita planilhas compartilhadas e cria abas dentro delas. Por isso: uma planilha só, com abas por comerciante. Para voltar a uma planilha por comerciante no futuro, basta um Drive compartilhado (Google Workspace); a interface de repositório isola a troca. |

> Credenciais: `GOOGLE_SERVICE_ACCOUNT_JSON` + `SPREADSHEET_ID` dão acesso à planilha; o edge-tts não precisa de credencial; a `GROQ_API_KEY` completa a lista. Tudo em variáveis de ambiente na Vercel.

#### Princípio importante: a IA não mexe na planilha diretamente
O LLM só **extrai a intenção** e devolve um JSON que é validado com Pydantic. A matemática do estoque e a gravação ficam em **código Python determinístico**. Isso evita contas erradas, permite testes automatizados e deixa trocar de modelo ou de provedor sem risco (a interface `Interpreter` isola isso).

Formato do JSON que o LLM devolve:
```json
{ "acao": "compra|venda|consulta|desfazer|desconhecido",
  "itens": [{ "produto": "arroz", "quantidade": 300, "unidade": "kg", "valor_total": 300.0 }] }
```
- Junto com a fala, o LLM recebe a **lista de produtos que já existem** na planilha, para ligar "arroz" à linha certa. O código ainda normaliza o nome (minúsculas, sem acento).
- Se o JSON vier inválido, o sistema tenta mais uma vez. Se falhar de novo, responde "não entendi, pode repetir?".
- Se o Groq devolver erro 429 (limite de uso), o job volta para o QStash tentar de novo.

---

### Modelo de dados (Google Sheets)

**Uma planilha** (`SPREADSHEET_ID`), aba `usuarios`: `telefone | nome | status | criado_em`

**Cadastro automático** (`app/storage/tenants.py`):
1. Procurar o telefone: primeiro no cache do Redis; se não estiver lá, na aba `usuarios` (criada sozinha na primeira execução).
2. Se o telefone não existir, **sob a trava por telefone no Redis** (evita cadastrar duas vezes se chegarem dois áudios ao mesmo tempo):
   - criar as abas `<telefone>_estoque` e `<telefone>_movimentacoes`, com cabeçalho (se já existirem, por uma tentativa anterior interrompida, são reaproveitadas);
   - adicionar a linha na aba `usuarios`, com o nome tirado do perfil do WhatsApp (`contacts[0].profile.name`) e status `ativo`.
3. Processar a mensagem normalmente. **Na primeira vez**, a resposta começa com uma saudação curta explicando como usar.
4. O status fica pronto para bloquear números no futuro (`bloqueado` → o bot ignora a mensagem).

**Abas de cada comerciante:**
- `<telefone>_movimentacoes`: o **livro-caixa**. Só recebe linhas novas, nunca edita as antigas, e é a fonte da verdade. Colunas: `id | data_hora | tipo(entrada/saida/ajuste) | produto | quantidade | unidade | valor_total | valor_unitario | texto_transcrito | whatsapp_msg_id | referencia`
- `<telefone>_estoque`: a visão atual do estoque. Colunas: `produto | unidade | quantidade | custo_medio | atualizado_em`

**Regras da V1:**
- **Compra**: soma a quantidade e recalcula o custo médio ponderado.
- **Venda**: subtrai a quantidade. Se o estoque não for suficiente, **não grava** e avisa quanto tem.
- **Unidade diferente da cadastrada**: não grava e pede para repetir na unidade certa.
- **Consulta**: só lê a planilha.
- **Desfazer**: grava uma movimentação inversa (`ajuste`) da última ação e não apaga nada.
- **Um áudio com vários itens**: é aceito.

**Resposta:** o texto é montado por **template no código**. Exemplo: *"Anotado! Entrou 300 quilos de arroz por 300 reais. Tinha 3 quilos, agora tem 303 quilos. Se estiver errado, diga 'desfazer'."* O bot manda o áudio e também o texto.

---

### Estrutura do projeto (Python)
```
api/index.py              # FastAPI: GET/POST /api/webhook, POST /api/worker
app/config.py             # variáveis de ambiente (pydantic-settings), inclui GROQ_MODEL
app/whatsapp/client.py    # baixar mídia, subir mídia, enviar texto/áudio, validar assinatura X-Hub-Signature-256
app/speech/stt.py         # interface SpeechToText + GroqWhisperSTT
app/speech/tts.py         # interface TextToSpeech + EdgeTTS (MP3 → OGG/Opus)
app/llm/interpreter.py    # interface Interpreter + GroqInterpreter (prompt + JSON + validação Pydantic)
app/domain/models.py      # Intencao, Item, Movimento, MudancaEstoque (Pydantic)
app/domain/inventory.py   # regras: compra/venda/desfazer, custo médio, validações
app/domain/replies.py     # templates de resposta (incluindo boas-vindas)
app/storage/repository.py # interface InventoryRepository (preparada para Postgres depois)
app/storage/sheets_repo.py # abas do comerciante (Sheets API) + criação de abas
app/storage/tenants.py    # busca/cria comerciante (aba usuarios + cache Redis)
app/google_auth.py        # credencial da conta de serviço
app/fila/qstash.py        # publicar job / verificar assinatura do QStash
app/fila/controle.py      # descarte de duplicadas e trava por telefone (Redis)
app/pipeline.py           # orquestra os passos 0–5
scripts/test_*.py         # testes manuais de cada peça
tests/                    # pytest (domínio + pipeline com fakes)
requirements.txt, vercel.json, .env.example
```
Dependências principais: `fastapi`, `groq`, `google-api-python-client`, `google-auth`, `upstash-redis`, `qstash`, `httpx`, `pydantic-settings`, `edge-tts`, `imageio-ffmpeg`, `pytest`.

---

### Passo a passo de implementação

**Fase 0: contas e credenciais (sem código)**
1. Meta for Developers: criar o app do tipo Business, adicionar o produto WhatsApp e anotar o `PHONE_NUMBER_ID`, o token e o `APP_SECRET`. Cadastrar os números de teste (até 5).
2. Groq: criar a `GROQ_API_KEY`.
3. Google Sheets:
   - ativar a Google Sheets API no projeto da conta de serviço e gerar a chave JSON (`GOOGLE_SERVICE_ACCOUNT_JSON`);
   - criar uma planilha em branco na sua conta, compartilhar com o `client_email` da conta de serviço como Editor e copiar o ID para `SPREADSHEET_ID`.
4. Upstash: criar o Redis e o QStash.
5. GitHub (repositório) e Vercel (importar o repositório).

**Fase 1: validar cada peça isolada**
7. `test_stt.py`: um áudio .ogg do WhatsApp vira texto.
8. `test_interpreter.py`: cerca de 10 frases reais viram o JSON certo (compra, venda, consulta, vários itens, frase confusa).
9. `test_sheets.py`: cadastrar um comerciante de teste (abas novas), ler e gravar.
10. `test_tts.py`: um texto vira um .ogg. Ouvir e conferir a voz.

**Fase 2: domínio e armazenamento**
11. `models.py`, `inventory.py` e `replies.py`, com testes pytest (custo médio, estoque insuficiente, unidade diferente, desfazer).
12. `sheets_repo.py` e `tenants.py`, com o cadastro automático.
13. `pipeline.py` testado localmente com fakes.

**Fase 3: WhatsApp e deploy**
14. `/api/webhook`: responder o GET de verificação, validar a assinatura, descartar mensagens repetidas, publicar no QStash e retornar 200.
15. `/api/worker`: verificar a assinatura do QStash e rodar o pipeline completo. Se der erro, responder algo amigável.
16. Deploy na Vercel, cadastrar a URL do webhook na Meta e assinar o evento `messages`.
17. Tratar mensagens de texto também (mesmo fluxo, sem STT).

**Fase 4: robustez**
18. Trava por telefone no Redis, cobrindo o cadastro e as atualizações de estoque.
19. Logs estruturados, com o tempo de cada etapa.
20. README com o setup completo.

**Próximas versões:**
- relatório do dia;
- confirmação antes de gravar valores altos;
- compartilhar a planilha com o e-mail do comerciante;
- migrar os dados para Postgres (Supabase/Neon) e manter o Sheets só como visualização;
- sair do plano Hobby quando o uso for comercial.

---

### Verificação
- `pytest`: regras do domínio e pipeline com fakes (sem rede).
- Os scripts da Fase 1 rodam contra os serviços reais.
- Teste de ponta a ponta com o número de teste da Meta:
  1. **Número A novo** manda "comprei 300 kg de arroz por 300 reais":
     - aparece uma linha em `usuarios`;
     - as abas `<telefone>_estoque` e `<telefone>_movimentacoes` são criadas;
     - a movimentação é gravada;
     - a resposta chega com saudação e o estado do estoque.
  2. Número A manda "vendi 5 kg de arroz": o estoque cai e não são criadas outras abas.
  3. Número A manda "vendi 1000 kg de arroz": chega o aviso e nada é gravado.
  4. Número A manda "desfazer": entra o ajuste inverso.
  5. **Número B** manda um áudio: ganha as abas próprias dele, e as de A não mudam.
  6. Reenviar o mesmo webhook: não duplica nada.
  7. Mandar dois áudios de um número novo ao mesmo tempo: o cadastro e as abas são criados **uma vez só** (a trava funciona).
- Meta de tempo: menos de 15 segundos entre o envio do áudio e a resposta.

### Primeira ação depois de aprovado
Copiar este plano como uma nova seção **"## Plano de implementação (V1)"** no final de `task.md`, e só então começar a Fase 0 e a Fase 1.
