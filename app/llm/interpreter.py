"""Transforma a fala transcrita em operações estruturadas (o LLM só interpreta; o código decide e calcula).

- v2 (padrão): `interpretar(texto, contexto) -> RespostaLLM`, com o contexto da conversa (pendências, histórico,
  catálogo) e vários tipos de operação.
- v1 (linha de base da avaliação): o prompt antigo, uma ação por mensagem, sem contexto.
"""

import json
import logging
import time
from dataclasses import dataclass
from typing import Protocol

import groq
from pydantic import ValidationError

from app.conversa.contexto import Contexto
from app.domain.models import Acao, Intencao
from app.domain.operacoes import Operacao, RespostaLLM, TipoOperacao, de_intencao
from app.errors import ServicoIndisponivel

log = logging.getLogger(__name__)

PROMPT_VERSAO = "v2"

PROMPT_V1 = """Você interpreta falas de pequenos comerciantes sobre o estoque da loja.
Responda APENAS com um JSON neste formato:
{"acao": "compra|venda|consulta|desfazer|desconhecido",
 "itens": [{"produto": "arroz", "quantidade": 300, "unidade": "kg", "valor_unitario": null, "valor_total": 300.0}]}

Regras:
- compra: comprou, chegou, recebeu, entrou mercadoria, repôs.
- venda: vendeu, saiu, o cliente levou.
- consulta: perguntas como "quanto tem de arroz?", "o que tem no estoque?". Se a pergunta for sobre o estoque todo, use "itens": [].
- desfazer: "desfazer", "cancela", "errei", "apaga o último". Use "itens": [].
- desconhecido: qualquer outra coisa. Use "itens": [].
- produto: nome curto, em minúsculas, no singular (ex.: "arroz", "feijão", "óleo de soja").
  Se o produto falado for o mesmo de um dos produtos cadastrados, use exatamente o nome cadastrado.
- unidade: uma destas: kg, g, l, ml, un, saco, cx, pct, fardo, duzia. Se não foi falada, use null.
- quantidade: número (ex.: "meio quilo" = 0.5, "uma dúzia" = 1 com unidade "duzia").
- valores em reais, como número (ex.: "150,59" = 150.59). NÃO faça contas: copie o valor falado.
  - valor_unitario: se o comerciante falou o preço por unidade ("cada", "o quilo", "a unidade", "o litro").
  - valor_total: se falou o valor da operação inteira ("paguei 50", "por 300 reais", "custou 80").
  - Se não falou valor, use null nos dois.
- Uma fala pode ter vários itens."""

PROMPT_V2 = """Você interpreta mensagens (transcritas de áudio, podem ter erros) de pequenos comerciantes que
anotam estoque e gastos pelo WhatsApp. Você recebe o CONTEXTO da conversa e a FALA atual.
Responda APENAS com um JSON:
{"operacoes": [{"tipo": "...", "itens": [], "valor": null, "categoria": null, "descricao": null,
  "pendencia_id": null, "movimento_ref": null, "campos": {}, "assunto": null, "periodo": null, "confianca": 0.9}]}

Tipos de operação:
- compra: entrou mercadoria (comprei, chegou, recebi, repus, adicionar). itens obrigatório.
- venda: saiu mercadoria (vendi, saiu, o cliente levou). itens obrigatório.
- despesa: gasto que NÃO é mercadoria de estoque (luz, aluguel, frete, gasolina, funcionário, imposto, sacola).
  "valor" = o valor gasto. "categoria" = uma destas: aluguel, energia, agua, internet, transporte_frete,
  funcionario, impostos, embalagem, manutencao, mercadoria, outros (null se não disse com o quê).
  "descricao" = o que foi, em poucas palavras (null se não disse). "gastei 100 reais de arroz" é COMPRA de arroz.
- retirada: o dono tirou dinheiro do caixa para ele ("tirei 50 pra mim"). "valor".
- completar: a fala responde uma PENDÊNCIA do contexto (o bot perguntou o valor, a quantidade, com o que foi
  o gasto). "pendencia_id" = o id da pendência. "campos" = só o que foi dito, entre: valor_unitario,
  valor_total, quantidade, unidade, categoria, descricao. Ex.: {"valor_unitario": 10}.
  Se existe pendência de valor de um produto e a fala dá um preço desse produto SEM dizer que vendeu,
  é completar, NÃO é venda.
- corrigir: muda uma anotação já feita ("errei, eram 12 quilos", "o arroz foi 60, não 50").
  "movimento_ref" = id do movimento em ultimos_movimentos, ou "ultimo". "campos" = os dados corretos,
  incluindo "produto" se citado. Se for só para apagar a última anotação, use desfazer.
- consulta: pergunta. "assunto": "estoque" (quanto tem, o que tem; itens com os produtos citados, ou [] para
  tudo) ou "gastos" (quanto gastei; "periodo": "hoje", "semana" ou "mes"; "categoria" se citada).
- desfazer: "desfazer", "apaga o último", "cancela a última anotação".
- confirmar: "sim", "isso", "pode anotar", "foi venda" respondendo a uma pendência de confirmação.
  "pendencia_id" = o id da confirmação.
- cancelar: "não", "deixa pra lá", "não sei o valor", "esquece" respondendo a uma pendência. "pendencia_id".
- conversa: cumprimento, agradecimento, dúvida ou qualquer outra coisa.

Itens: {"produto": "arroz", "quantidade": 300, "unidade": "kg", "valor_unitario": null, "valor_total": 300}
- produto: nome curto, minúsculas, singular. Se for um produto do catálogo, use exatamente o nome do catálogo.
- unidade: kg, g, l, ml, un, saco, cx, pct, fardo, duzia; null se não falou (o código usa a do catálogo).
- quantidade: número ("meio quilo" = 0.5); null se não falou.
- valores: copie o número falado, SEM fazer contas ("150,59" = 150.59; "8 e 50" = 8.5).
  valor_unitario se é preço por unidade ("cada", "o quilo", "a unidade"); valor_total se é o total
  ("paguei 50", "por 300", "custou 80"). Se não falou valor, null nos dois.

Regras:
- Uma fala pode ter várias operações (ex.: uma compra e uma venda).
- confianca: de 0 a 1, o quanto você tem certeza do tipo e dos números.
- Use o histórico: a última pergunta do bot diz o que uma resposta curta está respondendo.

Exemplos (contexto resumido):
1. Pendência {"id": 12, "resumo": "os 10 quilos de feijão", "faltando": ["valor"]}.
   Fala: "O feijão, o quilo foi 10 reais cada."
   {"operacoes":[{"tipo":"completar","pendencia_id":12,"campos":{"valor_unitario":10},"confianca":0.9}]}
2. Sem pendência. Fala: "vendi o feijão a 10 reais o quilo, 2 quilos"
   {"operacoes":[{"tipo":"venda","itens":[{"produto":"feijão","quantidade":2,"unidade":"kg",
   "valor_unitario":10,"valor_total":null}],"confianca":0.95}]}
3. Fala: "gastei 150,59"
   {"operacoes":[{"tipo":"despesa","valor":150.59,"categoria":null,"descricao":null,"confianca":0.95}]}
4. Pendência {"id": 7, "resumo": "o gasto de 150 reais e 59 centavos", "faltando": ["categoria"]}.
   Fala: "foi gasolina"
   {"operacoes":[{"tipo":"completar","pendencia_id":7,"campos":{"categoria":"transporte_frete",
   "descricao":"gasolina"},"confianca":0.9}]}
5. Pendência {"id": 3, "tipo": "confirmacao"}. Fala: "isso, pode anotar"
   {"operacoes":[{"tipo":"confirmar","pendencia_id":3,"confianca":0.95}]}
6. Pendência {"id": 4, "resumo": "os 5 quilos de carne", "faltando": ["valor"]}. Fala: "não lembro quanto paguei"
   {"operacoes":[{"tipo":"cancelar","pendencia_id":4,"confianca":0.9}]}
7. Fala: "comprei 2 quilos de arroz por 10 reais e vendi 1 quilo de feijão"
   {"operacoes":[{"tipo":"compra","itens":[{"produto":"arroz","quantidade":2,"unidade":"kg",
   "valor_unitario":null,"valor_total":10}],"confianca":0.95},{"tipo":"venda","itens":[{"produto":"feijão",
   "quantidade":1,"unidade":"kg","valor_unitario":null,"valor_total":null}],"confianca":0.95}]}
8. Fala: "quanto eu gastei essa semana com frete?"
   {"operacoes":[{"tipo":"consulta","assunto":"gastos","periodo":"semana","categoria":"transporte_frete",
   "confianca":0.95}]}"""


@dataclass
class Uso:
    """Medição da última chamada (para a avaliação e o log)."""

    segundos: float = 0.0
    tokens_entrada: int = 0
    tokens_saida: int = 0


class Interpreter(Protocol):
    def interpretar(self, texto: str, contexto: Contexto) -> RespostaLLM: ...


class GroqInterpreter:
    def __init__(self, api_key: str, model, client: groq.Groq | None = None):
        """`model`: nome do modelo, ou uma função que o devolve (lido das configurações a cada chamada)."""
        self._client = client or groq.Groq(api_key=api_key)
        self._model = model
        self.ultimo_uso = Uso()

    def interpretar(self, texto: str, contexto: Contexto) -> RespostaLLM:
        mensagens = [
            {"role": "system", "content": PROMPT_V2},
            {"role": "user", "content": f"CONTEXTO: {contexto.para_json()}\n\nFALA: {texto}"},
        ]
        for tentativa in range(2):
            conteudo = self._chamar(mensagens)
            try:
                return _limpar(RespostaLLM.model_validate_json(conteudo))
            except ValidationError as e:
                log.warning("JSON inválido do LLM (tentativa %d): %s | %s", tentativa + 1, e, conteudo[:500])
        return RespostaLLM(operacoes=[Operacao(tipo=TipoOperacao.CONVERSA, confianca=0.0)])

    def interpretar_v1(self, texto: str, produtos: list[str]) -> RespostaLLM:
        """Prompt antigo (linha de base da avaliação), convertido para operações v2."""
        mensagens = [
            {"role": "system", "content": PROMPT_V1},
            {"role": "user",
             "content": f"Produtos cadastrados: {json.dumps(produtos, ensure_ascii=False)}\n\nFala: {texto}"},
        ]
        for _ in range(2):
            try:
                return de_intencao(Intencao.model_validate_json(self._chamar(mensagens)))
            except ValidationError:
                continue
        return de_intencao(Intencao(acao=Acao.DESCONHECIDO))

    def _chamar(self, mensagens: list[dict]) -> str:
        inicio = time.perf_counter()
        try:
            resposta = self._client.chat.completions.create(
                model=self._model() if callable(self._model) else self._model,
                messages=mensagens,
                response_format={"type": "json_object"},
                temperature=0,
            )
        except (groq.RateLimitError, groq.APIConnectionError, groq.InternalServerError) as e:
            raise ServicoIndisponivel(f"Groq LLM: {e}") from e
        uso = getattr(resposta, "usage", None)
        self.ultimo_uso = Uso(time.perf_counter() - inicio, getattr(uso, "prompt_tokens", 0) or 0,
                              getattr(uso, "completion_tokens", 0) or 0)
        return resposta.choices[0].message.content or ""


def _limpar(resposta: RespostaLLM) -> RespostaLLM:
    """Descarta itens sem produto (o LLM às vezes devolve {"produto": ""})."""
    for op in resposta.operacoes:
        op.itens = [i for i in op.itens if i.produto and i.produto.strip()]
    return resposta
