import { useState, type FormEvent } from "react";
import { useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, baixar, ou } from "../api/cliente";
import { Abas, Estado, Selo, Tabela, data, num, reais } from "../componentes/base";

type Aba = "estoque" | "movimentos" | "relatorio" | "fiado" | "caixa" | "fechamentos" | "pendencias" | "conversa";
type Produto = { id: number; produto: string; unidade: string; quantidade: number; custo_medio: number | null; atualizado_em: string };
type Linha = Record<string, any>;
type Periodo = "hoje" | "semana" | "mes" | "mes_passado";

const MEIOS: Record<string, string> = { dinheiro: "Dinheiro", pix: "Pix", cartao: "Cartão", fiado: "Fiado", nao_informado: "Não informado" };
const r$ = (v: string | number | null | undefined) => (v === null || v === undefined ? "—" : reais(Number(v)));
const dia = (iso: string) => iso.slice(8, 10) + "/" + iso.slice(5, 7);

function Relatorio({ cid }: { cid: number }) {
  const [periodo, setPeriodo] = useState<Periodo>("mes");
  const q = useQuery({
    queryKey: ["comerciante", cid, "relatorio", periodo],
    queryFn: async () => (await ou(api.GET("/api/admin/v1/comerciantes/{comerciante_id}/relatorios/caixa", {
      params: { path: { comerciante_id: cid }, query: { periodo } } }))) as Linha,
  });
  const r = q.data;
  const linhasResumo: [string, string | number | null | undefined][] = r ? [
    ["Vendas", r.vendas], ...Object.entries(r.por_forma as Record<string, string>).map(([k, v]) => [`  ${MEIOS[k] ?? k}`, v] as [string, string]),
    ["Compras", r.compras], ["Despesas", r.despesas], ["Retiradas", r.retiradas], ["Recebido de fiado", r.recebido_fiado],
    ["Entrou", r.entrou], ["Saiu", r.saiu], ["Saldo", r.saldo], ["Lucro bruto estimado", r.lucro_bruto],
  ] : [];
  const avisos = r ? Object.entries(r.avisos as Record<string, number>).filter(([, n]) => n > 0) : [];
  const nomesAviso: Record<string, string> = { compras_sem_valor: "compras sem valor", vendas_sem_valor: "vendas sem valor", vendas_sem_custo: "vendas sem custo (fora do lucro)" };
  return (
    <>
      <label className="linha">Período
        <select value={periodo} onChange={(e) => setPeriodo(e.target.value as Periodo)}>
          <option value="hoje">Hoje</option><option value="semana">Esta semana</option>
          <option value="mes">Este mês</option><option value="mes_passado">Mês passado</option>
        </select>
      </label>
      <Estado carregando={q.isLoading} erro={q.error}>
        {r && <>
          <h2>{r.periodo.rotulo}</h2>
          <Tabela colunas={["", "Valor"]} linhas={linhasResumo.map(([nome, v]) => [nome, r$(v)])} />
          {avisos.length > 0 && <p className="erro">{avisos.map(([k, n]) => `${n} ${nomesAviso[k] ?? k}`).join(" · ")}</p>}
          <h2>Por dia</h2>
          <Tabela colunas={["Dia", "Vendas", "Fiado", "Compras", "Gastos", "Recebido fiado", "Saldo"]} vazio="Sem movimento no período."
            linhas={(r.dias as Linha[]).filter((d) => [d.vendas, d.compras, d.despesas, d.retiradas, d.recebido_fiado].some((v) => Number(v) !== 0))
              .map((d) => [dia(d.dia), r$(d.vendas), r$(d.vendido_fiado), r$(d.compras), r$(Number(d.despesas) + Number(d.retiradas)), r$(d.recebido_fiado), r$(d.saldo)])} />
          <h2>Por produto</h2>
          <Tabela colunas={["Produto", "Tipo", "Quantidade", "Valor", "Meios"]} vazio="Nada vendido ou comprado."
            linhas={(r.itens as Linha[]).map((i) => [i.produto, i.tipo, `${num(Number(i.quantidade))} ${i.unidade}`, r$(i.valor), (i.formas as string[]).map((f) => MEIOS[f] ?? f).join(", ")])} />
        </>}
      </Estado>
    </>
  );
}

function Ajuste({ cid, produto, fechar }: { cid: number; produto: Produto; fechar: () => void }) {
  const [quantidade, setQuantidade] = useState(String(produto.quantidade));
  const [custo, setCusto] = useState(produto.custo_medio === null ? "" : String(produto.custo_medio));
  const [motivo, setMotivo] = useState("");
  const consultas = useQueryClient();
  const m = useMutation({
    mutationFn: () => ou(api.POST("/api/admin/v1/comerciantes/{comerciante_id}/produtos/{produto_id}/ajustes", {
      params: { path: { comerciante_id: cid, produto_id: produto.id } },
      body: {
        motivo,
        quantidade_nova: quantidade !== String(produto.quantidade) ? quantidade.replace(",", ".") : null,
        custo_medio_novo: custo !== "" && custo !== String(produto.custo_medio) ? custo.replace(",", ".") : null,
      } as never,
    })),
    onSuccess: () => {
      consultas.invalidateQueries({ queryKey: ["comerciante", cid] });
      fechar();
    },
  });
  const enviar = (e: FormEvent) => { e.preventDefault(); m.mutate(); };
  return (
    <div className="dialogo" role="dialog" aria-modal="true" aria-labelledby="titulo-ajuste">
      <form onSubmit={enviar}>
        <h2 id="titulo-ajuste">Ajustar {produto.produto}</h2>
        <p className="suave">O ajuste vira um movimento no livro-caixa, com seu nome e o motivo. Nada é apagado.</p>
        <label>Quantidade ({produto.unidade})<input value={quantidade} onChange={(e) => setQuantidade(e.target.value)} inputMode="decimal" /></label>
        <label>Custo médio (R$ por {produto.unidade})<input value={custo} onChange={(e) => setCusto(e.target.value)} inputMode="decimal" /></label>
        <label>Motivo<input value={motivo} onChange={(e) => setMotivo(e.target.value)} required minLength={3} /></label>
        {m.error && <p className="erro" role="alert">{m.error.message}</p>}
        <span className="linha">
          <button className="primario" disabled={m.isPending}>Salvar ajuste</button>
          <button type="button" onClick={fechar}>Cancelar</button>
        </span>
      </form>
    </div>
  );
}

export function Comerciante({ podeEscrever }: { podeEscrever: boolean }) {
  const cid = Number(useParams().id);
  const [aba, setAba] = useState<Aba>("estoque");
  const [ajustando, setAjustando] = useState<Produto | null>(null);
  const cab = useQuery({ queryKey: ["comerciante", cid], queryFn: () => ou(api.GET("/api/admin/v1/comerciantes/{comerciante_id}", { params: { path: { comerciante_id: cid } } })) });
  const caminho = { params: { path: { comerciante_id: cid }, query: { limite: 200 } } };
  const dados = useQuery({
    queryKey: ["comerciante", cid, aba],
    queryFn: async (): Promise<Linha[]> => {
      if (aba === "estoque") return (await ou(api.GET("/api/admin/v1/comerciantes/{comerciante_id}/produtos", { params: { path: { comerciante_id: cid } } }))) as Linha[];
      if (aba === "movimentos") return ((await ou(api.GET("/api/admin/v1/comerciantes/{comerciante_id}/movimentos", caminho))) as { itens: Linha[] }).itens;
      if (aba === "caixa") return ((await ou(api.GET("/api/admin/v1/comerciantes/{comerciante_id}/lancamentos", caminho))) as { itens: Linha[] }).itens;
      if (aba === "fiado") return ((await ou(api.GET("/api/admin/v1/comerciantes/{comerciante_id}/clientes", { params: { path: { comerciante_id: cid } } }))) as { itens: Linha[] }).itens;
      if (aba === "fechamentos") return ((await ou(api.GET("/api/admin/v1/comerciantes/{comerciante_id}/fechamentos", { params: { path: { comerciante_id: cid } } }))) as { itens: Linha[] }).itens;
      if (aba === "relatorio") return [];
      if (aba === "pendencias") return ((await ou(api.GET("/api/admin/v1/pendencias", { params: { query: { comerciante_id: cid, status: null, limite: 200 } } }))) as { itens: Linha[] }).itens;
      return ((await ou(api.GET("/api/admin/v1/mensagens", { params: { query: { comerciante_id: cid, limite: 100 } } }))) as { itens: Linha[] }).itens;
    },
  });
  const c = cab.data as Linha | undefined;
  const linhas = dados.data ?? [];

  const tabelas: Record<Aba, () => JSX.Element> = {
    estoque: () => (
      <Tabela colunas={["Produto", "Quantidade", "Custo médio", "Atualizado", ""]} vazio="Sem produtos."
        linhas={linhas.map((p) => [p.produto, `${num(p.quantidade)} ${p.unidade}`, p.custo_medio === null ? <span className="suave">sem custo</span> : reais(p.custo_medio), data(p.atualizado_em),
          podeEscrever ? <button onClick={() => setAjustando(p as Produto)}>Ajustar</button> : null])} />
    ),
    movimentos: () => (
      <Tabela colunas={["Quando", "Tipo", "Produto", "Qtd.", "Unitário", "Total", "Meio", "Autor", "Texto / motivo"]} vazio="Sem movimentos."
        linhas={linhas.map((m) => [data(m.criado_em), m.tipo, m.produto, `${num(m.quantidade)} ${m.unidade}`, reais(m.valor_unitario), reais(m.valor_total),
          ["entrada", "saida"].includes(m.tipo) ? MEIOS[m.forma_pagamento] ?? m.forma_pagamento : "", m.autor, m.motivo || m.texto])} />
    ),
    relatorio: () => <Relatorio cid={cid} />,
    fiado: () => (
      <Tabela colunas={["Cliente", "Deve", "Deve desde", "Último movimento"]} vazio="Ninguém no fiado."
        linhas={linhas.map((c) => [c.nome, reais(c.saldo_devedor), data(c.divida_desde), data(c.atualizado_em)])} />
    ),
    fechamentos: () => (
      <Tabela colunas={["Dia", "Tipo", "Status", "Canal", "Tentativas", "Erro"]} vazio="Nenhum fechamento automático ainda. O comerciante liga pelo WhatsApp (ex.: me manda o fechamento todo dia às 8 da noite)."
        linhas={linhas.map((f) => [f.referencia, f.tipo, <Selo valor={f.status} />, f.canal, f.tentativas, f.erro])} />
    ),
    caixa: () => (
      <Tabela colunas={["Quando", "Tipo", "Categoria", "Descrição", "Valor"]} vazio="Sem lançamentos no caixa."
        linhas={linhas.map((l) => [data(l.criado_em), l.tipo, l.categoria, l.descricao || l.texto, reais(l.valor)])} />
    ),
    pendencias: () => (
      <Tabela colunas={["Criada", "Tipo", "O quê", "Falta", "Status", "Expira"]} vazio="Sem perguntas."
        linhas={linhas.map((p) => [data(p.criado_em), p.tipo, p.resumo, (p.campos_faltando ?? []).join(", "), <Selo valor={p.status} />, data(p.expira_em)])} />
    ),
    conversa: () => (
      <Tabela colunas={["Quando", "De", "Texto", "Intenção", "Status", "Entrega"]} vazio="Sem mensagens."
        linhas={[...linhas].reverse().map((m) => [data(m.criado_em), m.direcao === "entrada" ? "comerciante" : "bot", m.transcricao || m.texto,
          m.intencao ? <pre>{JSON.stringify(m.intencao.operacoes ?? m.intencao, null, 1)}</pre> : "",
          <span><Selo valor={m.status} />{m.etapa_erro && <span className="erro"> {m.etapa_erro}</span>}</span>, <Selo valor={m.status_entrega} />])} />
    ),
  };

  return (
    <>
      <Estado carregando={cab.isLoading} erro={cab.error}>
        <div className="linha" style={{ justifyContent: "space-between", marginBottom: 12 }}>
          <div>
            <h1 style={{ marginBottom: 4 }}>{c?.nome || "(sem nome)"}</h1>
            <span className="suave">{c?.telefone} · <Selo valor={c?.status} /></span>
          </div>
          <button onClick={() => baixar(`/api/admin/v1/comerciantes/${cid}/exportacao.zip`, `comerciante_${cid}.zip`)}>Exportar tudo (ZIP)</button>
        </div>
      </Estado>
      <Abas<Aba> abas={[["estoque", "Estoque"], ["movimentos", "Movimentos"], ["relatorio", "Relatório"], ["fiado", "Fiado"], ["caixa", "Despesas"], ["fechamentos", "Fechamentos"], ["pendencias", "Perguntas"], ["conversa", "Conversa"]]} atual={aba} mudar={setAba} />
      <Estado carregando={dados.isLoading} erro={dados.error}>{tabelas[aba]()}</Estado>
      {ajustando && <Ajuste cid={cid} produto={ajustando} fechar={() => setAjustando(null)} />}
    </>
  );
}
