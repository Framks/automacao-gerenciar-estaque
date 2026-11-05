"""Dublês compartilhados pelos testes da conversa."""

from dataclasses import replace

from app.conversa.modelos import EstadoConversa, Plano
from app.domain.financeiro import Lancamento


class LojaMemoria:
    """Loja em memória: aplica o plano como o Postgres faria (ids nas pendências, só acrescenta no livro)."""

    def __init__(self):
        self.estoque = {}
        self.movimentos = []
        self.lancamentos: list[Lancamento] = []
        self.pendencias = []  # todas, com status
        self._ids = 0
        self.gravacoes = 0

    def carregar_estado(self, agora: str) -> EstadoConversa:
        abertas = [replace(p) for p in self.pendencias if p.status == "aberta" and p.expira_em > agora]
        return EstadoConversa(dict(self.estoque), abertas, list(self.movimentos[-50:]), list(self.lancamentos))

    def gravar(self, plano: Plano, mensagem_id=None) -> None:
        self.gravacoes += 1
        self.movimentos += plano.movimentos
        self.estoque.update(plano.estoque)
        self.lancamentos += plano.lancamentos
        for alt in plano.lancamentos_alterados:
            for l in self.lancamentos:
                if l.id == alt.id:
                    l.categoria, l.descricao = alt.categoria, alt.descricao
        for f in plano.pendencias_fechadas:
            for p in self.pendencias:
                if p.id == f.id:
                    p.status = f.status
        for p in plano.pendencias_novas:
            self._ids += 1
            self.pendencias.append(replace(p, id=self._ids))

    def abertas(self):
        return [p for p in self.pendencias if p.status == "aberta"]
