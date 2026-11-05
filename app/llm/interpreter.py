"""Transforma a fala transcrita em uma `Intencao` estruturada."""

import json
import logging
from typing import Protocol

import groq
from pydantic import ValidationError

from app.domain.models import Acao, Intencao
from app.errors import ServicoIndisponivel

log = logging.getLogger(__name__)

PROMPT_SISTEMA = """Você interpreta falas de pequenos comerciantes sobre o estoque da loja.
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


class Interpreter(Protocol):
    def interpretar(self, texto: str, produtos: list[str]) -> Intencao: ...


class GroqInterpreter:
    def __init__(self, api_key: str, model: str, client: groq.Groq | None = None):
        self._client = client or groq.Groq(api_key=api_key)
        self._model = model

    def interpretar(self, texto: str, produtos: list[str]) -> Intencao:
        mensagens = [
            {"role": "system", "content": PROMPT_SISTEMA},
            {
                "role": "user",
                "content": f"Produtos cadastrados: {json.dumps(produtos, ensure_ascii=False)}\n\nFala: {texto}",
            },
        ]
        for tentativa in range(2):
            conteudo = self._chamar(mensagens)
            try:
                return Intencao.model_validate_json(conteudo)
            except ValidationError as e:
                log.warning("JSON inválido do LLM (tentativa %d): %s | %s", tentativa + 1, e, conteudo)
        return Intencao(acao=Acao.DESCONHECIDO)

    def _chamar(self, mensagens: list[dict]) -> str:
        try:
            resposta = self._client.chat.completions.create(
                model=self._model,
                messages=mensagens,
                response_format={"type": "json_object"},
                temperature=0,
            )
        except (groq.RateLimitError, groq.APIConnectionError, groq.InternalServerError) as e:
            raise ServicoIndisponivel(f"Groq LLM: {e}") from e
        return resposta.choices[0].message.content or ""
