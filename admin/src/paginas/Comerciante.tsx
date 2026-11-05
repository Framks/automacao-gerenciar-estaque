import { useState, type FormEvent } from "react";
import { useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, baixar, ou } from "../api/cliente";
import { Abas, Estado, Selo, Tabela, data, num, reais } from "../componentes/base";

type Aba = "estoque" | "movimentos" | "caixa" | "pendencias" | "conversa";
type Produto = { id: number; produto: string; unidade: string; quantidade: number; custo_medio: number | null; atualizado_em: string };
type Linha = Record<string, any>;

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
      <Tabela colunas={["Quando", "Tipo", "Produto", "Qtd.", "Unitário", "Total", "Autor", "Texto / motivo"]} vazio="Sem movimentos."
        linhas={linhas.map((m) => [data(m.criado_em), m.tipo, m.produto, `${num(m.quantidade)} ${m.unidade}`, reais(m.valor_unitario), reais(m.valor_total), m.autor, m.motivo || m.texto])} />
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
      <Abas<Aba> abas={[["estoque", "Estoque"], ["movimentos", "Movimentos"], ["caixa", "Caixa"], ["pendencias", "Perguntas"], ["conversa", "Conversa"]]} atual={aba} mudar={setAba} />
      <Estado carregando={dados.isLoading} erro={dados.error}>{tabelas[aba]()}</Estado>
      {ajustando && <Ajuste cid={cid} produto={ajustando} fechar={() => setAjustando(null)} />}
    </>
  );
}
