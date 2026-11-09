"""Motor da conversa: aplica as operações v2 de uma mensagem sobre o estado e decide o que perguntar.

Código puro (sem banco, sem rede, sem LLM). Entra: as operações entendidas, o estado do comerciante e a fala.
Sai: o `Plano` (tudo que deve ser gravado, numa transação só), o texto da resposta e os pedidos de relatório
(o relatório precisa do livro do período, que o pipeline carrega).

Regras principais:
- falta campo obrigatório: não grava; abre pendência e pergunta;
- falta campo desejável (valor da compra, categoria do gasto): grava e pergunta;
- completar nunca reescreve o livro-caixa: gera `ajuste_valor` referenciando o original;
- uma pergunta por resposta; resolvida uma pendência, pergunta a próxima;
- dúvida (confiança baixa, valor sem verbo, preço fora do custo, valor alto): confirma antes de gravar;
- fiado: a venda só é gravada com o cliente e o valor; a dívida fica num livro próprio (só acrescenta).
"""

import re
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from app.conversa.ambiguidade import avaliar_duvida
from app.conversa.modelos import (
    AlteracaoMovimento,
    EstadoConversa,
    FechamentoPendencia,
    Parametros,
    Pendencia,
    PedidoRelatorio,
    Plano,
)
from app.domain import clientes as cli
from app.domain import fiado as fd
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
from app.domain.pagamento import NAO_INFORMADO, normalizar_pagamento, resolver_forma
from app.domain.periodos import parse_horario, resolver_periodo
from app.domain.units import chave_produto, nome_produto, normalizar_unidade

FORMATO = "%Y-%m-%d %H:%M:%S"
SUFIXO_DESFAZER = " Se estiver errado, diga: desfazer."
FORMATOS_RELATORIO = ("lista", "resumo", "itens", "por_dia", "grafico", "planilha")


@dataclass
class Saida:
    plano: Plano
    texto: str
    pergunta: str = ""
    resumo: list[dict[str, Any]] = field(default_factory=list)  # o que foi feito com cada operação (para o log)
    pedidos: list[PedidoRelatorio] = field(default_factory=list)  # relatórios que o pipeline deve montar


def _tem_valor(item) -> bool:
    return item.valor_unitario is not None or item.valor_total is not None


def _bool(valor) -> bool:
    if isinstance(valor, bool):
        return valor
    return str(valor).strip().lower() in ("true", "sim", "1", "ligar", "liga", "ligado", "quero")


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
        self.clientes = dict(estado.clientes)
        self.fiado = list(estado.fiado)
        self.abertas = list(estado.pendencias)
        self.plano = Plano()
        self.frases: list[str] = []
        self.perguntas: list[str] = []
        self.pedidos: list[PedidoRelatorio] = []
        self.resolveu = False
        self.resumo: list[dict[str, Any]] = []
        self._seq = 0

    def estado(self) -> EstadoConversa:
        return EstadoConversa(self.estoque, self.abertas, self.movimentos, self.lancamentos, self.clientes, self.fiado)

    # --- ids e gravação ---------------------------------------------------------------------------------

    def proximo_id(self) -> str:
        id_ = f"{self.msg_id}-{self._seq}"
        self._seq += 1
        return id_

    def gravar_resultado(self, r: Resultado, forma: str = NAO_INFORMADO, origem: str = "") -> list[Movimento]:
        novos = []
        for m in r.movimentos:
            m = replace(m, id=self.proximo_id())
            if m.tipo in (TipoMovimento.ENTRADA, TipoMovimento.SAIDA):
                m = replace(m, forma_pagamento=forma, forma_pagamento_origem=origem)
            novos.append(m)
        self.plano.movimentos += novos
        self.movimentos += novos
        for produto in r.estoque_alterado:
            chave = chave_produto(produto.produto)
            self.estoque[chave] = produto
            self.plano.estoque[chave] = produto
        return novos

    def lancar_fiado(self, chave: str, nome: str, tipo: str, valor: Decimal, *, movimento_ref: str = "",
                     forma: str = "", referencia: str = "") -> fd.Cliente:
        cliente = self.clientes.get(chave) or fd.Cliente(nome=nome, chave=chave)
        lanc = fd.LancamentoFiado(self.proximo_id(), self.agora, chave, cliente.nome, tipo, reais(valor),
                                  movimento_ref=movimento_ref, forma_pagamento=forma, texto=self.texto,
                                  wa_msg_id=self.msg_id, referencia=referencia)
        novo = fd.aplicar(cliente, lanc)
        self.clientes[chave] = novo
        self.plano.clientes[chave] = novo
        self.plano.fiado.append(lanc)
        self.fiado.append(lanc)
        return novo

    def alterar_forma(self, m: Movimento, forma: str, origem: str = "dito") -> None:
        self.plano.movimentos_alterados.append(AlteracaoMovimento(m.id, forma, origem, motivo=self.texto))
        self.movimentos = [replace(x, forma_pagamento=forma, forma_pagamento_origem=origem) if x.id == m.id else x
                           for x in self.movimentos]

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
            if op.tipo not in (TipoOperacao.CONFIRMAR, TipoOperacao.CANCELAR) and not self._responde_confirmacao(ex, op):
                ex.cancelar_confirmacoes()
            antes = len(ex.frases), len(ex.perguntas)
            self._despachar(ex, op)
            ex.resumo.append({"tipo": op.tipo.value, "frases": len(ex.frases) - antes[0],
                              "perguntas": len(ex.perguntas) - antes[1]})
        return self._montar_saida(ex)

    @staticmethod
    def _responde_confirmacao(ex: _Execucao, op: Operacao) -> bool:
        """"é o João Pedro" / "é cliente novo" respondendo a "Você quis dizer...?" não cancela a pergunta."""
        return op.tipo == TipoOperacao.COMPLETAR and bool(op.campos.get("cliente") or op.campos.get("cliente_novo")) \
            and any(pd.tipo == "confirmacao" and "cliente" in pd.campos_faltando for pd in ex.abertas)

    # --- despacho ---------------------------------------------------------------------------------------

    def _despachar(self, ex: _Execucao, op: Operacao) -> None:
        tratar = {
            TipoOperacao.COMPRA: self._compra_venda, TipoOperacao.VENDA: self._compra_venda,
            TipoOperacao.DESPESA: self._despesa, TipoOperacao.RETIRADA: self._despesa,
            TipoOperacao.RECEBIMENTO: self._recebimento, TipoOperacao.PERDOAR: self._perdoar,
            TipoOperacao.CONFIGURAR: self._configurar,
            TipoOperacao.COMPLETAR: self._completar, TipoOperacao.CORRIGIR: self._corrigir,
            TipoOperacao.CONSULTA: self._consulta, TipoOperacao.DESFAZER: self._desfazer,
            TipoOperacao.CONFIRMAR: self._confirmar, TipoOperacao.CANCELAR: self._cancelar,
            TipoOperacao.CONVERSA: self._conversa,
        }[op.tipo]
        tratar(ex, op)

    def _reexecutar(self, ex: _Execucao, op: Operacao) -> None:
        """Roda de novo uma operação que estava esperando (resposta a uma pergunta ou um "sim")."""
        if op.tipo in (TipoOperacao.COMPRA, TipoOperacao.VENDA):
            self._compra_venda(ex, op, confirmado=True)
        elif op.tipo == TipoOperacao.RECEBIMENTO:
            self._recebimento(ex, op, confirmado=True)
        elif op.tipo == TipoOperacao.PERDOAR:
            self._perdoar(ex, op, confirmado=True)
        else:
            self._despachar(ex, op)

    # --- compra e venda ---------------------------------------------------------------------------------

    def _forma(self, op: Operacao) -> tuple[str, str]:
        if op.tipo == TipoOperacao.VENDA:
            return resolver_forma(op.forma_pagamento, self.p.pagamento_padrao)
        forma = op.forma_pagamento if op.forma_pagamento in ("dinheiro", "pix", "cartao", "fiado") else None
        return (forma, "dito") if forma else (NAO_INFORMADO, "")

    def _compra_venda(self, ex: _Execucao, op: Operacao, confirmado: bool = False) -> None:
        if not confirmado:
            duvida = avaliar_duvida(op, ex.texto, ex.estado(), self.p, lambda o: self._descrever(o, ex.estoque))
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

        forma, origem = self._forma(op)
        fiado = op.tipo == TipoOperacao.VENDA and forma == "fiado"
        cliente = None
        if fiado:
            cliente = self._preparar_fiado(ex, op, confirmado)
            if cliente is None:
                return

        r = inventory.aplicar(para_intencao(op), ex.estoque, texto=ex.texto, msg_id=ex.msg_id, agora=ex.agora)
        if not r.ok:
            ex.frases.append(replies.montar(r))
            return
        novos = ex.gravar_resultado(r, forma, origem)

        extra = ""
        if fiado and cliente is not None:
            for m in novos:
                atual = ex.lancar_fiado(cliente.chave, cliente.nome, "divida", m.valor_total, movimento_ref=m.id)
            extra = replies.fiado_anotado(atual.nome, atual.saldo)
        elif origem == "dito":
            extra = replies.frase_pagamento(op.tipo.value, forma)
        ex.frases.append(replies.montar(r, extra))

        if op.tipo == TipoOperacao.COMPRA and self.p.perguntar_valor_compra:
            for falta in av.itens:
                if "valor" not in falta.desejaveis:
                    continue
                m = novos[falta.indice]
                ex.abrir("completar", op, ["valor"], replies.resumo_entrada(m.quantidade, m.unidade, m.produto),
                         replies.pergunta_valor("compra", m.produto), movimento_ref=m.id, item=falta.indice)

        if op.tipo == TipoOperacao.VENDA and forma == NAO_INFORMADO and self.p.perguntar_pagamento and novos:
            m = novos[0]
            ex.abrir("completar", op, ["forma_pagamento"], f"a venda de {replies.qtd(m.quantidade, m.unidade)} de "
                     f"{m.produto}", replies.pergunta_pagamento(self.p.formas_pagamento), movimento_ref=m.id)

    def _preparar_fiado(self, ex: _Execucao, op: Operacao, confirmado: bool) -> cli.Resolucao | None:
        """Cliente e valor são obrigatórios na venda fiado. None = perguntou (ou não dá) e nada foi gravado."""
        produtos = " e ".join(nome_produto(i.produto) for i in op.itens)
        if not self.p.fiado:
            ex.frases.append(replies.FIADO_DESLIGADO)
            return None
        if not (op.cliente or "").strip():
            ex.abrir("completar", op, ["cliente"], f"a venda fiado de {produtos}", replies.PERGUNTA_CLIENTE_FIADO)
            return None
        res = cli.resolver(op.cliente, ex.clientes, estrito=confirmado)
        if res.sugestoes:
            sugerida = op.model_copy(update={"campos": {"sugestao_cliente": res.sugestoes[0]}})
            ex.abrir("confirmacao", sugerida, ["cliente"], f"a venda fiado de {produtos}",
                     replies.pergunta_cliente_parecido(res.nome, res.sugestoes))
            return None
        sem_valor = [i for i, item in enumerate(op.itens) if not _tem_valor(item)]
        if sem_valor:
            nome = nome_produto(op.itens[sem_valor[0]].produto)
            ex.abrir("completar", op, ["valor"], f"a venda fiado de {nome}", replies.pergunta_valor_fiado(nome),
                     item=sem_valor[0])
            return None
        return res

    # --- despesas -----------------------------------------------------------------------------------------

    def _despesa(self, ex: _Execucao, op: Operacao) -> None:
        if not self.p.despesas:
            ex.frases.append(replies.DESPESAS_DESLIGADAS)
            return
        av = avaliar(op, ex.estoque)
        if "valor" in av.obrigatorios_faltando:
            ex.abrir("completar", op, ["valor"], "um gasto sem valor", replies.pergunta_valor_despesa())
            return
        valor = reais(op.valor)
        forma = self._forma(op)[0]
        forma = "" if forma == NAO_INFORMADO else forma
        if op.tipo == TipoOperacao.RETIRADA:
            lanc = Lancamento(ex.proximo_id(), ex.agora, "retirada", valor, "retirada", op.descricao or "",
                              ex.texto, ex.msg_id, forma_pagamento=forma)
            ex.plano.lancamentos.append(lanc)
            ex.lancamentos.append(lanc)
            ex.frases.append(replies.retirada_anotada(valor))
            return
        categoria = normalizar_categoria(op.categoria or op.descricao, list(self.p.categorias_despesa))
        lanc = Lancamento(ex.proximo_id(), ex.agora, "despesa", valor, categoria, (op.descricao or "").strip(),
                          ex.texto, ex.msg_id, forma_pagamento=forma)
        ex.plano.lancamentos.append(lanc)
        ex.lancamentos.append(lanc)
        falada = NOMES_CATEGORIA.get(categoria) if categoria != "outros" else None
        ex.frases.append(replies.despesa_anotada(valor, falada, lanc.descricao))
        if "categoria" in av.desejaveis_faltando:
            ex.abrir("completar", op, ["categoria"], f"o gasto de {replies.fmt_reais(valor)}",
                     replies.pergunta_categoria(valor), lancamento_ref=lanc.id)

    # --- fiado: pagamento e perdão ---------------------------------------------------------------------------

    def _cliente_existente(self, ex: _Execucao, op: Operacao, resumo: str) -> fd.Cliente | None:
        if not self.p.fiado:
            ex.frases.append(replies.FIADO_DESLIGADO)
            return None
        if not (op.cliente or "").strip():
            ex.abrir("completar", op, ["cliente"], resumo, replies.PERGUNTA_QUEM_PAGOU)
            return None
        chave, sugestoes = cli.buscar_existente(op.cliente, ex.clientes)
        if chave is None:
            if len(sugestoes) > 1:
                ex.abrir("completar", op, ["cliente"], resumo, replies.pergunta_qual_cliente(sugestoes))
            else:
                ex.frases.append(replies.cliente_nao_achado(cli.nome_cliente(op.cliente), sugestoes))
            return None
        return ex.clientes[chave]

    def _recebimento(self, ex: _Execucao, op: Operacao, confirmado: bool = False) -> None:
        cliente = self._cliente_existente(ex, op, "um pagamento de fiado")
        if cliente is None:
            return
        if cliente.saldo <= 0:
            ex.frases.append(replies.nao_deve(cliente.nome))
            return
        valor = cliente.saldo if op.tudo else (reais(op.valor) if op.valor else None)
        if valor is None or valor <= 0:
            ex.abrir("completar", op, ["valor"], f"o pagamento de {cliente.nome}",
                     replies.pergunta_quanto_pagou(cliente.nome))
            return
        if valor > cliente.saldo and not confirmado:
            quita = op.model_copy(update={"valor": cliente.saldo, "tudo": False, "cliente": cliente.nome})
            ex.abrir("confirmacao", quita, [], f"o pagamento de {cliente.nome}",
                     replies.confirmar_quitacao(cliente.nome, cliente.saldo))
            return
        valor = min(valor, cliente.saldo)
        forma, _ = resolver_forma(op.forma_pagamento, self.p.pagamento_padrao)
        forma = "" if forma == NAO_INFORMADO else forma
        depois = ex.lancar_fiado(cliente.chave, cliente.nome, "pagamento", -valor, forma=forma)
        ex.frases.append(replies.pagamento_fiado(cliente.nome, valor, forma, depois.saldo))

    def _perdoar(self, ex: _Execucao, op: Operacao, confirmado: bool = False) -> None:
        cliente = self._cliente_existente(ex, op, "o perdão de uma dívida")
        if cliente is None:
            return
        if cliente.saldo <= 0:
            ex.frases.append(replies.nao_deve(cliente.nome))
            return
        if not confirmado:
            ex.abrir("confirmacao", op.model_copy(update={"cliente": cliente.nome}), [],
                     f"o perdão da dívida de {cliente.nome}", replies.confirmar_perdao(cliente.nome, cliente.saldo))
            return
        valor = cliente.saldo
        ex.lancar_fiado(cliente.chave, cliente.nome, "perdao", -valor)
        ex.frases.append(replies.perdoado(cliente.nome, valor))

    # --- preferências do comerciante ---------------------------------------------------------------------

    def _configurar(self, ex: _Execucao, op: Operacao) -> None:
        campos = {k: v for k, v in op.campos.items() if v is not None and v != ""}
        if not campos:
            ex.frases.append(replies.config_atual(self.p.fechamento_horario, self.p.fechamento_mensal,
                                                  self.p.fechamento_formato, self.p.pagamento_padrao))
            return
        for chave, valor in campos.items():
            if chave in ("fechamento_horario", "horario", "fechamento_diario_horario"):
                h = parse_horario(str(valor))
                if h is None:
                    ex.frases.append(replies.HORARIO_NAO_ENTENDI)
                elif h.ambiguo is not None:
                    pendente = op.model_copy(update={"campos": {"fechamento_horario": str(valor)}})
                    ex.abrir("completar", pendente, ["horario"], "o horário do fechamento",
                             replies.pergunta_manha_noite(h.ambiguo))
                elif h.desligar:
                    ex.plano.configuracoes["fechamento_diario_horario"] = ""
                    ex.frases.append(replies.FECHAMENTO_DESLIGADO)
                else:
                    ex.plano.configuracoes["fechamento_diario_horario"] = h.hhmm
                    ex.frases.append(replies.fechamento_ligado(h.hhmm))
            elif chave in ("fechamento_mensal", "mensal"):
                ligado = _bool(valor)
                ex.plano.configuracoes["fechamento_mensal"] = ligado
                ex.frases.append(replies.FECHAMENTO_MENSAL_LIGADO if ligado else replies.FECHAMENTO_MENSAL_DESLIGADO)
            elif chave in ("fechamento_formato", "formato"):
                resumo = str(valor).strip().lower() in ("resumo", "resumido", "so o total", "só o total")
                ex.plano.configuracoes["fechamento_formato"] = "resumo" if resumo else "lista"
                ex.frases.append(replies.FECHAMENTO_FORMATO_RESUMO if resumo else replies.FECHAMENTO_FORMATO_ITENS)
            elif chave == "pagamento_padrao":
                forma = normalizar_pagamento(str(valor))
                if forma is None:
                    ex.frases.append(replies.NAO_ENTENDI_PAGAMENTO)
                elif forma == "fiado":
                    ex.frases.append(replies.PAGAMENTO_PADRAO_FIADO)
                else:
                    ex.plano.configuracoes["pagamento_padrao"] = forma
                    ex.frases.append(replies.pagamento_padrao_definido(forma))
            else:
                ex.frases.append(replies.CONFIG_NAO_PERMITIDA)

    # --- completar, corrigir --------------------------------------------------------------------------------

    def _completar(self, ex: _Execucao, op: Operacao) -> None:
        campos = op.campos
        if campos.get("cliente") or campos.get("cliente_novo"):
            confirmacao = next((pd for pd in reversed(ex.abertas) if pd.tipo == "confirmacao"
                                and "cliente" in pd.campos_faltando
                                and (op.pendencia_id is None or pd.id == op.pendencia_id)), None)
            if confirmacao is not None and (op.pendencia_id is not None or ex.buscar("completar", None) is None):
                pendente = Operacao.model_validate(confirmacao.operacao)
                nome = pendente.cliente if campos.get("cliente_novo") else str(campos.get("cliente") or "")
                ex.fechar(confirmacao, "resolvida")
                self._reexecutar(ex, pendente.model_copy(update={"cliente": nome, "campos": {}}))
                return

        pd = ex.buscar("completar", op.pendencia_id)
        if pd is None:
            ex.frases.append(replies.NADA_PENDENTE)
            return

        if pd.movimento_ref:
            original = next((m for m in ex.movimentos if m.id == pd.movimento_ref), None)
            if original is None:
                ex.fechar(pd, "cancelada")
                ex.frases.append(replies.NAO_ACHEI_ANOTACAO)
                return
            if "forma_pagamento" in pd.campos_faltando or "cliente" in pd.campos_faltando:
                forma = normalizar_pagamento(campos.get("forma_pagamento"))
                if "cliente" in pd.campos_faltando and campos.get("cliente"):
                    forma = "fiado"
                if forma is None or forma == NAO_INFORMADO:
                    ex.frases.append(replies.NAO_ENTENDI_PAGAMENTO + " " + pd.pergunta)
                    return
                ex.fechar(pd, "resolvida")
                self._mudar_forma(ex, original, forma, campos.get("cliente"))
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
        elif dados.get("tipo") == TipoOperacao.CONFIGURAR.value:
            antigo = str((dados.get("campos") or {}).get("fechamento_horario") or "")
            novo = str(campos.get("fechamento_horario") or campos.get("horario") or campos.get("descricao") or "")
            if novo and not re.search(r"\d", novo):
                novo = f"{antigo} {novo}"
            dados["campos"] = {"fechamento_horario": novo or antigo}
        else:
            for chave in ("valor", "categoria", "descricao"):
                if campos.get(chave) is not None:
                    dados[chave] = campos[chave]
            if dados.get("valor") is None and campos.get("valor_total") is not None:
                dados["valor"] = campos["valor_total"]
        for chave in ("cliente", "forma_pagamento", "tudo"):
            if campos.get(chave) not in (None, ""):
                dados[chave] = campos[chave]
        ex.fechar(pd, "resolvida")
        self._reexecutar(ex, Operacao.model_validate({**dados, "confianca": 1.0}))

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
        if original.tipo == TipoMovimento.SAIDA:  # venda fiado: a dívida acompanha o valor novo
            for divida in fd.dividas_da_venda(ex.fiado, {original.id}):
                diferenca = reais(m.valor_total - fd.valor_da_divida(ex.fiado, divida))
                if diferenca:
                    depois = ex.lancar_fiado(divida.cliente, divida.cliente_nome, "ajuste", diferenca,
                                             movimento_ref=original.id)
                    ex.frases.append(replies.fiado_ajustado(depois.nome, depois.saldo))
        return True

    def _mudar_forma(self, ex: _Execucao, original: Movimento, forma: str, cliente_nome: str | None) -> bool:
        """Troca o meio de pagamento de uma compra/venda já anotada. De ou para fiado mexe na dívida."""
        movs = [m for m in ex.movimentos if m.whatsapp_msg_id == original.whatsapp_msg_id and m.tipo == original.tipo]
        atual = original.forma_pagamento
        if forma == atual:
            ex.frases.append(replies.ja_estava(forma))
            return True
        extra = ""
        if original.tipo == TipoMovimento.SAIDA and forma == "fiado":
            if not self.p.fiado:
                ex.frases.append(replies.FIADO_DESLIGADO)
                return False
            if not (cliente_nome or "").strip():
                pendente = Operacao(tipo=TipoOperacao.CORRIGIR, movimento_ref=original.id,
                                    campos={"forma_pagamento": "fiado"})
                ex.abrir("completar", pendente, ["cliente"], f"a venda de {original.produto}",
                         replies.PERGUNTA_CLIENTE_FIADO, movimento_ref=original.id)
                return False
            vigentes = inventory.valores_vigentes(ex.movimentos)
            if any(vigentes.get(m.id, (None, None))[1] is None for m in movs):
                ex.frases.append(replies.FIADO_PRECISA_VALOR)
                return False
            res = cli.resolver(cliente_nome, ex.clientes, estrito=True)
            for m in movs:
                depois = ex.lancar_fiado(res.chave, res.nome, "divida", vigentes[m.id][1], movimento_ref=m.id)
            extra = replies.fiado_anotado(depois.nome, depois.saldo)
        elif atual == "fiado":
            for divida in fd.dividas_da_venda(ex.fiado, {m.id for m in movs}):
                valor = fd.valor_da_divida(ex.fiado, divida)
                depois = ex.lancar_fiado(divida.cliente, divida.cliente_nome, "ajuste", -valor,
                                         movimento_ref=divida.movimento_ref, referencia=divida.id)
                extra = replies.fiado_estornado(depois.nome, depois.saldo)
        for m in movs:
            ex.alterar_forma(m, forma)
        ex.frases.append(" ".join(f for f in (replies.forma_alterada(forma), extra) if f))
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
        feito = False
        if any(campos.get(k) is not None for k in ("valor", "valor_unitario", "valor_total")):
            feito = True
            if self._aplicar_valor(ex, original, campos):
                for pd in [pd for pd in ex.abertas if pd.movimento_ref == original.id and "valor" in pd.campos_faltando]:
                    ex.fechar(pd, "resolvida")
        if campos.get("quantidade") is not None:
            feito = True
            self._corrigir_quantidade(ex, original, campos["quantidade"])
        forma = normalizar_pagamento(campos.get("forma_pagamento") or op.forma_pagamento)
        if forma and forma != NAO_INFORMADO:
            feito = True
            original = next((m for m in ex.movimentos if m.id == original.id), original)
            if self._mudar_forma(ex, original, forma, campos.get("cliente") or op.cliente):
                for pd in [pd for pd in ex.abertas if pd.movimento_ref == original.id
                           and "forma_pagamento" in pd.campos_faltando]:
                    ex.fechar(pd, "resolvida")
        if not feito:
            ex.frases.append(replies.CORRIGIR_PRODUTO)

    def _corrigir_quantidade(self, ex: _Execucao, original: Movimento, valor) -> None:
        try:
            nova = parse_valor(valor)
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

    # --- consultas --------------------------------------------------------------------------------------------

    def _consulta(self, ex: _Execucao, op: Operacao) -> None:
        if op.assunto == "caixa":
            formato = op.formato if op.formato in FORMATOS_RELATORIO else None
            if formato is None:
                hoje = datetime.strptime(ex.agora, FORMATO).date()
                formato = "por_dia" if resolver_periodo(op.periodo, hoje).tipo == "mes" else "lista"
            ex.pedidos.append(PedidoRelatorio(op.periodo, formato))
            return
        if op.assunto == "fiado":
            self._consulta_fiado(ex, op)
            return
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

    def _consulta_fiado(self, ex: _Execucao, op: Operacao) -> None:
        if op.cliente:
            chave, sugestoes = cli.buscar_existente(op.cliente, ex.clientes)
            if chave is None:
                ex.frases.append(replies.cliente_nao_achado(cli.nome_cliente(op.cliente), sugestoes))
                return
            c = ex.clientes[chave]
            if c.saldo <= 0:
                ex.frases.append(replies.nao_deve(c.nome))
                return
            ultimas = [(l.data_hora, fd.valor_da_divida(ex.fiado, l)) for l in fd.ultimas_dividas(ex.fiado, chave)]
            ex.frases.append(replies.saldo_cliente(c.nome, c.saldo, c.divida_desde, ultimas))
            return
        lista = fd.devedores(ex.clientes, dias=op.dias, agora=ex.agora)
        ex.frases.append(replies.devedores([(c.nome, c.saldo) for c in lista], fd.total(lista), op.dias))

    # --- desfazer ---------------------------------------------------------------------------------------------

    def _desfazer(self, ex: _Execucao, op: Operacao) -> None:
        f_desfeitos = fd.desfeitos(ex.fiado)
        ultimo_f = next((l for l in reversed(ex.fiado)
                         if l.tipo in ("pagamento", "perdao") and l.id not in f_desfeitos), None)
        desfeitos_l = {l.referencia for l in ex.lancamentos if l.tipo == "ajuste"}
        ultimo_l = next((l for l in reversed(ex.lancamentos)
                         if l.tipo in ("despesa", "retirada") and l.id not in desfeitos_l), None)
        desfeitos_m = {m.referencia for m in ex.movimentos if m.tipo == TipoMovimento.AJUSTE and m.referencia}
        ultimo_m = next((m for m in reversed(ex.movimentos) if m.tipo in (TipoMovimento.ENTRADA, TipoMovimento.SAIDA)
                         and m.whatsapp_msg_id not in desfeitos_m), None)

        candidatos = [(c.data_hora, tipo) for c, tipo in ((ultimo_f, "fiado"), (ultimo_l, "lancamento"),
                                                           (ultimo_m, "movimento")) if c is not None]
        escolhido = max(candidatos, key=lambda c: c[0])[1] if candidatos else "movimento"

        if escolhido == "fiado":
            depois = ex.lancar_fiado(ultimo_f.cliente, ultimo_f.cliente_nome, "ajuste", -ultimo_f.valor,
                                     referencia=ultimo_f.id)
            ex.frases.append(replies.pagamento_desfeito(depois.nome, -ultimo_f.valor, depois.saldo, ultimo_f.tipo))
            return

        if escolhido == "lancamento":
            ajuste = desfazer_lancamento(ultimo_l, texto=ex.texto, msg_id=ex.msg_id, agora=ex.agora)
            ajuste.id = ex.proximo_id()
            ex.plano.lancamentos.append(ajuste)
            ex.lancamentos.append(ajuste)
            for pd in [pd for pd in ex.abertas if pd.lancamento_ref == ultimo_l.id]:
                ex.fechar(pd, "cancelada")
            ex.frases.append(replies.lancamento_desfeito(ultimo_l.valor, ultimo_l.tipo))
            return

        r = inventory.desfazer(ex.movimentos, ex.estoque, texto=ex.texto, msg_id=ex.msg_id, agora=ex.agora)
        extra = ""
        if r.ok:
            alvo = r.movimentos[0].referencia
            ex.gravar_resultado(r)
            ids = {m.id for m in ex.movimentos if m.whatsapp_msg_id == alvo}
            for pd in [pd for pd in ex.abertas if pd.movimento_ref in ids]:
                ex.fechar(pd, "cancelada")
            for divida in fd.dividas_da_venda(ex.fiado, ids):
                valor = fd.valor_da_divida(ex.fiado, divida)
                depois = ex.lancar_fiado(divida.cliente, divida.cliente_nome, "ajuste", -valor,
                                         movimento_ref=divida.movimento_ref, referencia=divida.id)
                extra = " " + replies.fiado_estornado(depois.nome, depois.saldo)
        ex.frases.append(replies.montar(r) + extra)

    # --- sim, não, conversa ----------------------------------------------------------------------------------

    def _confirmar(self, ex: _Execucao, op: Operacao) -> None:
        pd = ex.buscar("confirmacao", op.pendencia_id)
        if pd is None:
            ex.frases.append(replies.NADA_PARA_CONFIRMAR)
            return
        ex.fechar(pd, "resolvida")
        pendente = Operacao.model_validate(pd.operacao)
        sugestao = pendente.campos.get("sugestao_cliente")
        if sugestao:
            pendente = pendente.model_copy(update={"cliente": sugestao, "campos": {}})
        self._reexecutar(ex, pendente)

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
        if not texto and not ex.pedidos:
            texto = "Não entendi. " + replies.AJUDA
        return Saida(plano=ex.plano, texto=texto, pergunta=pergunta, resumo=ex.resumo, pedidos=ex.pedidos)

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
