import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ou } from "../api/cliente";
import { Estado, Tabela } from "../componentes/base";

type Definicao = { chave: string; tipo: string; descricao: string; padrao: unknown; opcoes: string[]; minimo: number | null; maximo: number | null; por_comerciante: boolean; global: unknown; comerciantes: Record<string, unknown> };
type Flag = { chave: string; descricao: string; padrao: boolean; global: boolean | null; comerciantes: Record<string, boolean> };

function texto(v: unknown) {
  return Array.isArray(v) ? v.join(", ") : v === null || v === undefined ? "" : String(v);
}

function converter(tipo: string, bruto: string): unknown {
  if (tipo === "int" || tipo === "float" || tipo === "decimal") return Number(bruto.replace(",", "."));
  if (tipo === "list") return bruto.split(",").map((s) => s.trim()).filter(Boolean);
  return bruto;
}

function CampoConfig({ d, comerciante, salvar, remover, podeEscrever }: { d: Definicao; comerciante: string; salvar: (v: unknown) => void; remover: () => void; podeEscrever: boolean }) {
  const atual = comerciante ? d.comerciantes[comerciante] ?? d.global ?? d.padrao : d.global ?? d.padrao;
  const temProprio = comerciante ? comerciante in d.comerciantes : d.global !== null;
  const [valor, setValor] = useState(texto(atual));
  const id = `cfg-${d.chave}`;
  return (
    <div className="linha">
      {d.tipo === "enum" ? (
        <select id={id} aria-label={d.chave} value={valor} onChange={(e) => setValor(e.target.value)} disabled={!podeEscrever}>
          {d.opcoes.map((o) => <option key={o}>{o}</option>)}
        </select>
      ) : (
        <input id={id} aria-label={d.chave} value={valor} onChange={(e) => setValor(e.target.value)} disabled={!podeEscrever}
               inputMode={["int", "float", "decimal"].includes(d.tipo) ? "decimal" : undefined} />
      )}
      {podeEscrever && <button onClick={() => salvar(converter(d.tipo, valor))}>Salvar</button>}
      {podeEscrever && temProprio && <button onClick={remover}>{comerciante ? "Usar o global" : "Voltar ao padrão"}</button>}
    </div>
  );
}

export function Configuracoes({ podeEscrever }: { podeEscrever: boolean }) {
  const [comerciante, setComerciante] = useState("");
  const consultas = useQueryClient();
  const q = useQuery({ queryKey: ["configuracoes"], queryFn: () => ou(api.GET("/api/admin/v1/configuracoes")) });
  const lista = useQuery({ queryKey: ["comerciantes", "ativo", ""], queryFn: () => ou(api.GET("/api/admin/v1/comerciantes", { params: { query: { status: "ativo", limite: 200 } } })) });
  const recarregar = () => consultas.invalidateQueries({ queryKey: ["configuracoes"] });
  const cid = comerciante ? Number(comerciante) : null;
  const definir = useMutation({
    mutationFn: ({ chave, valor }: { chave: string; valor: unknown }) =>
      ou(api.PUT("/api/admin/v1/configuracoes/{chave}", { params: { path: { chave }, query: { comerciante_id: cid } }, body: { valor } })),
    onSuccess: recarregar,
  });
  const remover = useMutation({
    mutationFn: (chave: string) => ou(api.DELETE("/api/admin/v1/configuracoes/{chave}", { params: { path: { chave }, query: { comerciante_id: cid } } })),
    onSuccess: recarregar,
  });
  const flag = useMutation({
    mutationFn: ({ chave, ligado }: { chave: string; ligado: boolean | null }) => ligado === null
      ? ou(api.DELETE("/api/admin/v1/flags/{chave}", { params: { path: { chave }, query: { comerciante_id: cid } } }))
      : ou(api.PUT("/api/admin/v1/flags/{chave}", { params: { path: { chave }, query: { comerciante_id: cid } }, body: { ligado } })),
    onSuccess: recarregar,
  });
  const dados = q.data as { versao: string; configuracoes: Definicao[]; flags: Flag[] } | undefined;
  const comerciantes = ((lista.data as { itens?: { id: number; nome: string; telefone: string }[] } | undefined)?.itens ?? []);
  const erro = definir.error ?? remover.error ?? flag.error;

  return (
    <>
      <h1>Configurações</h1>
      <p className="suave">As mudanças valem em até 1 minuto, sem novo deploy. Versão atual: {dados?.versao ?? "…"}</p>
      <label style={{ maxWidth: 360, marginBottom: 12 }}>
        Escopo
        <select value={comerciante} onChange={(e) => setComerciante(e.target.value)}>
          <option value="">Global (todos os comerciantes)</option>
          {comerciantes.map((c) => <option key={c.id} value={c.id}>{c.nome || c.telefone}</option>)}
        </select>
      </label>
      {erro && <p className="erro" role="alert">{erro.message}</p>}
      <Estado carregando={q.isLoading} erro={q.error}>
        <h2>Funções (liga e desliga)</h2>
        <Tabela colunas={["Função", "Descrição", "Estado"]} linhas={(dados?.flags ?? []).map((f) => {
          const proprio = comerciante ? f.comerciantes[comerciante] : f.global;
          const efetivo = proprio ?? (comerciante ? f.global ?? f.padrao : f.padrao);
          return [f.chave, f.descricao,
            <label style={{ flexDirection: "row", alignItems: "center" }}>
              <input type="checkbox" checked={efetivo} disabled={!podeEscrever} aria-label={`Ligar ${f.chave}`}
                     onChange={(e) => flag.mutate({ chave: f.chave, ligado: e.target.checked })} />
              {efetivo ? "ligada" : "desligada"}{proprio === null || proprio === undefined ? " (herdado)" : ""}
              {podeEscrever && proprio !== null && proprio !== undefined && (
                <button type="button" onClick={() => flag.mutate({ chave: f.chave, ligado: null })}>herdar</button>
              )}
            </label>];
        })} />
        <h2>Valores</h2>
        <Tabela colunas={["Chave", "Descrição", "Padrão", "Valor"]} linhas={(dados?.configuracoes ?? [])
          .filter((d) => !comerciante || d.por_comerciante)
          .map((d) => [d.chave, d.descricao, texto(d.padrao),
            <CampoConfig key={`${d.chave}-${comerciante}-${dados?.versao}`} d={d} comerciante={comerciante} podeEscrever={podeEscrever}
                         salvar={(valor) => definir.mutate({ chave: d.chave, valor })} remover={() => remover.mutate(d.chave)} />])} />
      </Estado>
    </>
  );
}
