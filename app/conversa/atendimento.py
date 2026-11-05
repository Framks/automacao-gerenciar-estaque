"""Motor da conversa: aplica as operações v2 de uma mensagem sobre o estado e decide o que perguntar.

Código puro (sem banco, sem rede, sem LLM). Entra: as operações entendidas, o estado do comerciante e a fala.
Sai: o `Plano` (tudo que deve ser gravado, numa transação só) e o texto da resposta.

Regras principais:
- falta campo obrigatório: não grava; abre pendência e pergunta;
- falta campo desejável (valor da compra, categoria do gasto): grava e pergunta;
- completar nunca reescreve o livro-caixa: gera `ajuste_valor` referenciando o original;
- uma pergunta por resposta; resolvida uma pendência, pergunta a próxima;
- dúvida (confiança baixa, valor sem verbo, preço fora do custo, valor alto): confirma antes de gravar.
"""

from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from typing import Any

from app.conversa.ambiguidade import avaliar_duvida
from app.conversa.modelos import EstadoConversa, FechamentoPendencia, Parametros, Pendencia, Plano
from app.domain import inventory, replies
from app.domain.dinheiro import parse_valor, reais, resolver_valores, ValoresIncoerentes
from app.domain.financeiro import (
    NOMES_CATEGORIA,
    AlteracaoLancamento,
    Lancamento,
    desfazer_lancamento,
    inicio_periodo,
    normalizar_categoria,
    total_gastos,
)
from app.domain.models import Movimento, Resultado, TipoMovimento
from app.domain.operacoes import Operacao, RespostaLLM, TipoOperacao, avaliar, para_intencao
from app.domain.units import chave_produto, nome_produto, normalizar_unidade

FORMATO = "%Y-%m-%d %H:%M:%S"
SUFIXO_DESFAZER = " Se estiver errado, diga: desfazer."


@dataclass
class Saida:
    plano: Plano
    texto: str
    pergunta: str = ""
    resumo: list[dict[str, Any]] = field(default_factory=list)  # o que foi feito com cada operação (para o log)


class _Execucao:
    """Estado de trabalho de uma mensagem (cópias: o estado original não é alterado)."""

    def __init__(self, estado: EstadoConversa, p: Parametros, texto: str, msg_id: str, agora: str):
        self.p = p
        self.texto = texto
        self.msg_id = msg_id
        self.agora = agora
        self.estoque = dict(estado.estoque)
        self.movimentos = list(estado.movimentos)
        self.lancamentos = list(estado.lancamentos)
        self.abertas = list(estado.pendencias)
        self.plano = Plano()
        self.frases: list[str] = []
        self.perguntas: list[str] = []
        self.resolveu = False
        self.resumo: list[dict[str, Any]] = []
        self._seq = 0

    # --- ids e gravação ---------------------------------------------------------------------------------

    def proximo_id(self) -> str:
        id_ = f"{self.msg_id}-{self._seq}"
        self._seq += 1
        return id_

    def gravar_resultado(self, r: Resultado) -> list[Movimento]:
        novos = []
        for m in r.movimentos:
            m = replace(m, id=self.proximo_id())
            novos.append(m)
        self.plano.movimentos += novos
        self.movimentos += novos
        for produto in r.estoque_alterado:
            chave = chave_produto(produto.produto)
            self.estoque[chave] = produto
            self.plano.estoque[chave] = produto
        return novos

    # --- pendências -------------------------------------------------------------------------------------

    def abrir(self, tipo: str, op: Operacao, campos: list[str], resumo: str, pergunta: str, *,
              movimento_ref: str = "", lancamento_ref: str = "", item: int = 0) -> None:
        if not self.p.pendencias_ativas and tipo == "completar":
            return
        expira = datetime.strptime(self.agora, FORMATO) + timedelta(hours=self.p.pendencia_validade_horas)
        pd = Pendencia(
            tipo=tipo, operacao=op.model_dump(mode="json"), campos_faltando=campos, resumo=resumo, pergunta=pergunta,
            expira_em=expira.strftime(FORMATO), criado_em=self.agora, movimento_ref=movimento_ref,
            lancamento_ref=lancamento_ref, item=item, wa_msg_id=self.msg_id,
            chave_local=f"{self.msg_id}#{len(self.plano.pendencias_novas)}",
        )
        self.plano.pendencias_novas.append(pd)
        self.abertas.append(pd)
        self.perguntas.append(pergunta)

    def fechar(self, pd: Pendencia, status: str) -> None:
        if pd in self.abertas:
            self.abertas.remove(pd)
        if pd.id is None:
            if pd in self.plano.pendencias_novas:
                self.plano.pendencias_novas.remove(pd)
                if pd.pergunta in self.perguntas:
                    self.perguntas.remove(pd.pergunta)
            return
        self.plano.pendencias_fechadas.append(FechamentoPendencia(pd.id, status))
        if status == "resolvida":
            self.resolveu = True

    def buscar(self, tipo: str, pendencia_id: int | None) -> Pendencia | None:
        candidatas = [pd for pd in self.abertas if pd.tipo == tipo]
        if pendencia_id is not None:
            achada = next((pd for pd in candidatas if pd.id == pendencia_id), None)
            if achada:
                return achada
        return candidatas[-1] if candidatas else None

    def cancelar_confirmacoes(self) -> None:
        for pd in [pd for pd in self.abertas if pd.tipo == "confirmacao"]:
            self.fechar(pd, "cancelada")


class Atendimento:
    def __init__(self, parametros: Parametros = Parametros()):
        self.p = parametros

    def processar(self, resposta: RespostaLLM, estado: EstadoConversa, *, texto: str, msg_id: str,
                  agora: str) -> Saida:
        ex = _Execucao(estado, self.p, texto, msg_id, agora)
        operacoes = resposta.operacoes or [Operacao(tipo=TipoOperacao.CONVERSA)]
        for op in operacoes:
            if op.tipo not in (TipoOperacao.CONFIRMAR, TipoOperacao.CANCELAR):
                ex.cancelar_confirmacoes()
            antes = len(ex.frases), len(ex.perguntas)
            self._despachar(ex, op)
            ex.resumo.append({"tipo": op.tipo.value, "frases": len(ex.frases) - antes[0],
                              "perguntas": len(ex.perguntas) - antes[1]})
        return self._montar_saida(ex)

    # --- despacho ---------------------------------------------------------------------------------------

    def _despachar(self, ex: _Execucao, op: Operacao) -> None:
        tratar = {
            TipoOperacao.COMPRA: self._compra_venda, TipoOperacao.VENDA: self._compra_venda,
            TipoOperacao.DESPESA: self._despesa, TipoOperacao.RETIRADA: self._despesa,
            TipoOperacao.COMPLETAR: self._completar, TipoOperacao.CORRIGIR: self._corrigir,
            TipoOperacao.CONSULTA: self._consulta, TipoOperacao.DESFAZER: self._desfazer,
            TipoOperacao.CONFIRMAR: self._confirmar, TipoOperacao.CANCELAR: self._cancelar,
            TipoOperacao.CONVERSA: self._conversa,
        }[op.tipo]
        tratar(ex, op)

    def _compra_venda(self, ex: _Execucao, op: Operacao, confirmado: bool = False) -> None:
        if not confirmado:
            duvida = avaliar_duvida(op, ex.texto, EstadoConversa(ex.estoque, ex.abertas, ex.movimentos,
                                                                 ex.lancamentos), self.p,
                                    lambda o: self._descrever(o, ex.estoque))
            if duvida:
                ex.abrir("confirmacao", op, [], self._descrever(op, ex.estoque), duvida.pergunta)
                return

        av = avaliar(op, ex.estoque)
        if not av.pronta:
            falta = next((f for f in av.itens if f.obrigatorios), None)
            if falta is None or "produto" in falta.obrigatorios:
                ex.frases.append("Não entendi qual produto. " + replies.AJUDA)
                return
            item = op.itens[falta.indice]
            nome = nome_produto(item.produto)
            ex.abrir("completar", op, ["quantidade"], f"{'a compra' if op.tipo == TipoOperacao.COMPRA else 'a venda'}"
                     f" de {nome}", replies.pergunta_quantidade(nome, normalizar_unidade(item.unidade)),
                     item=falta.indice)
            return

        r = inventory.aplicar(para_intencao(op), ex.estoque, texto=ex.texto, msg_id=ex.msg_id, agora=ex.agora)
        if not r.ok:
            ex.frases.append(replies.montar(r))
            return
        novos = ex.gravar_resultado(r)
        ex.frases.append(replies.montar(r))

        if op.tipo == TipoOperacao.COMPRA and self.p.perguntar_valor_compra:
            for falta in av.itens:
                if "valor" not in falta.desejaveis:
                    continue
                m = novos[falta.indice]
                ex.abrir("completar", op, ["valor"], replies.resumo_entrada(m.quantidade, m.unidade, m.produto),
                         replies.pergunta_valor("compra", m.produto), movimento_ref=m.id, item=falta.indice)

    def _despesa(self, ex: _Execucao, op: Operacao) -> None:
        if not self.p.despesas:
            ex.frases.append(replies.DESPESAS_DESLIGADAS)
            return
        av = avaliar(op, ex.estoque)
        if "valor" in av.obrigatorios_faltando:
            ex.abrir("completar", op, ["valor"], "um gasto sem valor", replies.pergunta_valor_despesa())
            return
        valor = reais(op.valor)
        if op.tipo == TipoOperacao.RETIRADA:
            lanc = Lancamento(ex.proximo_id(), ex.agora, "retirada", valor, "retirada", op.descricao or "",
                              ex.texto, ex.msg_id)
            ex.plano.lancamentos.append(lanc)
            ex.lancamentos.append(lanc)
            ex.frases.append(replies.retirada_anotada(valor))
            return
        categoria = normalizar_categoria(op.categoria or op.descricao, list(self.p.categorias_despesa))
        lanc = Lancamento(ex.proximo_id(), ex.agora, "despesa", valor, categoria, (op.descricao or "").strip(),
                          ex.texto, ex.msg_id)
        ex.plano.lancamentos.append(lanc)
        ex.lancamentos.append(lanc)
        falada = NOMES_CATEGORIA.get(categoria) if categoria != "outros" else None
        ex.frases.append(replies.despesa_anotada(valor, falada, lanc.descricao))
        if "categoria" in av.desejaveis_faltando:
            ex.abrir("completar", op, ["categoria"], f"o gasto de {replies.fmt_reais(valor)}",
                     replies.pergunta_categoria(valor), lancamento_ref=lanc.id)

    def _completar(self, ex: _Execucao, op: Operacao) -> None:
        pd = ex.buscar("completar", op.pendencia_id)
        if pd is None:
            ex.frases.append(replies.NADA_PENDENTE)
            return
        campos = op.campos

        if pd.movimento_ref:
            original = next((m for m in ex.movimentos if m.id == pd.movimento_ref), None)
            if original is None:
                ex.fechar(pd, "cancelada")
                ex.frases.append(replies.NAO_ACHEI_ANOTACAO)
                return
            if not self._aplicar_valor(ex, original, campos):
                return
            ex.fechar(pd, "resolvida")
            return

        if pd.lancamento_ref:
            categoria = normalizar_categoria(campos.get("categoria") or campos.get("descricao"),
                                             list(self.p.categorias_despesa))
            descricao = str(campos.get("descricao") or campos.get("categoria") or "").strip()
            ex.plano.lancamentos_alterados.append(AlteracaoLancamento(pd.lancamento_ref, categoria, descricao,
                                                                      motivo=ex.texto))
            ex.frases.append(replies.despesa_completada(descricao, NOMES_CATEGORIA.get(categoria, categoria)))
            ex.fechar(pd, "resolvida")
            return

        # operação que ainda não foi gravada: junta o que chegou e tenta de novo
        dados = dict(pd.operacao)
        if dados.get("itens"):
            itens = [dict(i) for i in dados["itens"]]
            item = itens[pd.item]
            for chave in ("quantidade", "unidade", "valor_unitario", "valor_total", "produto"):
                if campos.get(chave) is not None:
                    item[chave] = campos[chave]
            if campos.get("valor") is not None and item.get("valor_total") is None:
                item["valor_total"] = campos["valor"]
            dados["itens"] = itens
        else:
            for chave in ("valor", "categoria", "descricao"):
                if campos.get(chave) is not None:
                    dados[chave] = campos[chave]
            if dados.get("valor") is None and campos.get("valor_total") is not None:
                dados["valor"] = campos["valor_total"]
        ex.fechar(pd, "resolvida")
        nova = Operacao.model_validate({**dados, "confianca": 1.0})
        if nova.tipo in (TipoOperacao.COMPRA, TipoOperacao.VENDA):
            self._compra_venda(ex, nova, confirmado=True)
        else:
            self._despesa(ex, nova)

    def _aplicar_valor(self, ex: _Execucao, original: Movimento, campos: dict) -> bool:
        try:
            unit = parse_valor(campos.get("valor_unitario"))
            total = parse_valor(campos.get("valor_total") if campos.get("valor_total") is not None
                                else campos.get("valor"))
        except ValueError:
            unit = total = None
        if unit is None and total is None:
            ex.frases.append(f"Não entendi o valor de {original.produto}. Pode repetir?")
            return False
        anterior = inventory.valores_vigentes(ex.movimentos).get(original.id, (None, None))[1]
        r = inventory.ajustar_valor(original, ex.estoque, valor_unitario=unit, valor_total=total,
                                    valor_anterior=anterior, texto=ex.texto, msg_id=ex.msg_id, agora=ex.agora)
        if not r.ok:
            ex.frases.append(" ".join(r.problemas))
            return False
        (m,) = ex.gravar_resultado(r)
        produto = ex.estoque.get(chave_produto(original.produto))
        tipo = "compra" if original.tipo == TipoMovimento.ENTRADA else "venda"
        ex.frases.append(replies.valor_completado(
            original.produto, original.unidade, original.quantidade, m.valor_unitario, m.valor_total,
            produto.custo_medio if produto is not None else None, tipo))
        return True

    def _corrigir(self, ex: _Execucao, op: Operacao) -> None:
        campos = op.campos
        alvos = [m for m in ex.movimentos if m.tipo in (TipoMovimento.ENTRADA, TipoMovimento.SAIDA)]
        if campos.get("produto") and not (op.movimento_ref and op.movimento_ref != "ultimo"):
            chave = chave_produto(str(campos["produto"]))
            com_produto = [m for m in alvos if chave_produto(m.produto) == chave]
            alvos = com_produto or alvos
        original = next((m for m in alvos if m.id == op.movimento_ref), None) or (alvos[-1] if alvos else None)
        if original is None:
            ex.frases.append(replies.NAO_ACHEI_ANOTACAO)
            return
        if any(campos.get(k) is not None for k in ("valor", "valor_unitario", "valor_total")):
            if self._aplicar_valor(ex, original, campos):
                for pd in [pd for pd in ex.abertas if pd.movimento_ref == original.id]:
                    ex.fechar(pd, "resolvida")
            return
        if campos.get("quantidade") is not None:
            try:
                nova = parse_valor(campos["quantidade"])
            except ValueError:
                nova = None
            if not nova:
                ex.frases.append(f"Não entendi a quantidade de {original.produto}.")
                return
            r = inventory.corrigir_quantidade(original, ex.estoque, quantidade_nova=nova, texto=ex.texto,
                                              msg_id=ex.msg_id, agora=ex.agora)
            if not r.ok:
                ex.frases.append(" ".join(r.problemas))
                return
            ex.gravar_resultado(r)
            mud = r.mudancas[0]
            ex.frases.append(replies.quantidade_corrigida(mud.produto, mud.unidade, mud.antes, mud.depois))
            return
        ex.frases.append(replies.CORRIGIR_PRODUTO)

    def _consulta(self, ex: _Execucao, op: Operacao) -> None:
        if op.assunto == "gastos":
            agora = datetime.strptime(ex.agora, FORMATO)
            categoria = normalizar_categoria(op.categoria, list(self.p.categorias_despesa)) if op.categoria else None
            t = total_gastos(ex.lancamentos, inicio_periodo(op.periodo, agora), categoria)
            maiores = sorted(t.por_categoria.items(), key=lambda kv: kv[1], reverse=True)[:3]
            ex.frases.append(replies.gastos(
                t.total, t.quantidade, op.periodo or "mes",
                [(NOMES_CATEGORIA.get(c, c), v) for c, v in maiores],
                NOMES_CATEGORIA.get(categoria) if categoria else None))
            return
        r = inventory.aplicar(para_intencao(op), ex.estoque, texto=ex.texto, msg_id=ex.msg_id, agora=ex.agora)
        ex.frases.append(replies.montar(r))

    def _desfazer(self, ex: _Execucao, op: Operacao) -> None:
        desfeitos_l = {l.referencia for l in ex.lancamentos if l.tipo == "ajuste"}
        ultimo_l = next((l for l in reversed(ex.lancamentos)
                         if l.tipo in ("despesa", "retirada") and l.id not in desfeitos_l), None)
        desfeitos_m = {m.referencia for m in ex.movimentos if m.tipo == TipoMovimento.AJUSTE and m.referencia}
        ultimo_m = next((m for m in reversed(ex.movimentos) if m.tipo in (TipoMovimento.ENTRADA, TipoMovimento.SAIDA)
                         and m.whatsapp_msg_id not in desfeitos_m), None)

        if ultimo_l and (ultimo_m is None or ultimo_l.data_hora >= ultimo_m.data_hora):
            ajuste = desfazer_lancamento(ultimo_l, texto=ex.texto, msg_id=ex.msg_id, agora=ex.agora)
            ajuste.id = ex.proximo_id()
            ex.plano.lancamentos.append(ajuste)
            ex.lancamentos.append(ajuste)
            for pd in [pd for pd in ex.abertas if pd.lancamento_ref == ultimo_l.id]:
                ex.fechar(pd, "cancelada")
            ex.frases.append(replies.lancamento_desfeito(ultimo_l.valor, ultimo_l.tipo))
            return

        r = inventory.desfazer(ex.movimentos, ex.estoque, texto=ex.texto, msg_id=ex.msg_id, agora=ex.agora)
        if r.ok:
            alvo = r.movimentos[0].referencia
            ex.gravar_resultado(r)
            ids = {m.id for m in ex.movimentos if m.whatsapp_msg_id == alvo}
            for pd in [pd for pd in ex.abertas if pd.movimento_ref in ids]:
                ex.fechar(pd, "cancelada")
        ex.frases.append(replies.montar(r))

    def _confirmar(self, ex: _Execucao, op: Operacao) -> None:
        pd = ex.buscar("confirmacao", op.pendencia_id)
        if pd is None:
            ex.frases.append(replies.NADA_PARA_CONFIRMAR)
            return
        ex.fechar(pd, "resolvida")
        self._compra_venda(ex, Operacao.model_validate(pd.operacao), confirmado=True)

    def _cancelar(self, ex: _Execucao, op: Operacao) -> None:
        pd = None
        if op.pendencia_id is not None:
            pd = next((p for p in ex.abertas if p.id == op.pendencia_id), None)
        pd = pd or (ex.abertas[-1] if ex.abertas else None)
        if pd is not None:
            ex.fechar(pd, "cancelada")
        ex.frases.append(replies.CANCELADO)

    def _conversa(self, ex: _Execucao, op: Operacao) -> None:
        ex.frases.append(replies.CONVERSA)

    # --- resposta ---------------------------------------------------------------------------------------

    def _montar_saida(self, ex: _Execucao) -> Saida:
        pergunta = ex.perguntas[0] if ex.perguntas else ""
        if not pergunta and ex.resolveu and ex.abertas:
            pergunta = "Mais uma coisa: " + ex.abertas[-1].pergunta
        frases = list(ex.frases)
        if pergunta and frases:
            frases = [f.replace(SUFIXO_DESFAZER, "") for f in frases]
        texto = " ".join([*frases, pergunta]).strip()
        return Saida(plano=ex.plano, texto=texto or ("Não entendi. " + replies.AJUDA), pergunta=pergunta,
                     resumo=ex.resumo)

    # --- descrição de operação para perguntas -----------------------------------------------------------

    @staticmethod
    def _descrever(op: Operacao, estoque: dict) -> str:
        partes = []
        for item in op.itens:
            produto = estoque.get(chave_produto(item.produto))
            unidade = normalizar_unidade(item.unidade) or (produto.unidade if produto else "un")
            try:
                _, total = resolver_valores(item.quantidade, item.valor_unitario, item.valor_total)
            except ValoresIncoerentes:
                total = None
            partes.append(replies.descrever_item(op.tipo.value, nome_produto(item.produto), item.quantidade,
                                                 unidade, total))
        return " e ".join(partes) or f"uma {op.tipo.value}"
