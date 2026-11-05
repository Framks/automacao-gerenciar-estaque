import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, ou } from "../api/cliente";
import { Estado, Selo, Tabela, data } from "../componentes/base";

const ETAPAS = ["", "cadastro", "download_midia", "transcricao", "contexto", "interpretacao", "dominio", "banco"];

export function Mensagens() {
  const [status, setStatus] = useState("erro");
  const [etapa, setEtapa] = useState("");
  const [de, setDe] = useState("");
  const [aberta, setAberta] = useState<number | null>(null);
  const q = useQuery({
    queryKey: ["mensagens", status, etapa, de],
    queryFn: () => ou(api.GET("/api/admin/v1/mensagens", { params: { query: { status: status || null, etapa: etapa || null, de: de || null, direcao: "entrada", limite: 100 } } })),
  });
  const detalhe = useQuery({
    queryKey: ["mensagem", aberta],
    enabled: aberta !== null,
    queryFn: () => ou(api.GET("/api/admin/v1/mensagens/{mensagem_id}", { params: { path: { mensagem_id: aberta! } } })),
  });
  const itens = ((q.data as { itens?: Record<string, any>[] } | undefined)?.itens ?? []);
  const d = detalhe.data as Record<string, any> | undefined;

  return (
    <>
      <h1>Mensagens</h1>
      <div className="linha" style={{ marginBottom: 12 }}>
        <label>Status
          <select value={status} onChange={(e) => setStatus(e.target.value)}>
            {["erro", "reprocessando", "ignorada", "processada", ""].map((s) => <option key={s} value={s}>{s || "todos"}</option>)}
          </select>
        </label>
        <label>Etapa do erro
          <select value={etapa} onChange={(e) => setEtapa(e.target.value)}>
            {ETAPAS.map((s) => <option key={s} value={s}>{s || "todas"}</option>)}
          </select>
        </label>
        <label>Desde<input type="date" value={de} onChange={(e) => setDe(e.target.value)} /></label>
      </div>
      <Estado carregando={q.isLoading} erro={q.error}>
        <Tabela colunas={["Quando", "Telefone", "Tipo", "Status", "Etapa", "Erro", ""]} vazio="Nenhuma mensagem com esse filtro."
          linhas={itens.map((m) => [data(m.criado_em), m.telefone_mascarado, m.tipo, <Selo valor={m.status} />, m.etapa_erro || "—", m.erro || "—",
            <button onClick={() => setAberta(m.id)}>Detalhes</button>])} />
      </Estado>
      {aberta !== null && (
        <div className="dialogo" role="dialog" aria-modal="true" aria-label="Detalhe da mensagem">
          <div>
            <Estado carregando={detalhe.isLoading} erro={detalhe.error}>
              <h2>Mensagem {aberta}</h2>
              <p><strong>Transcrição:</strong> {d?.transcricao || d?.texto || "—"}</p>
              <p><strong>Erro:</strong> {d?.erro || "—"} {d?.etapa_erro && <span className="erro">({d.etapa_erro})</span>}</p>
              <p><strong>Tempos por etapa (s):</strong></p>
              <pre>{JSON.stringify(d?.tempos ?? {}, null, 1)}</pre>
              <p><strong>Intenção:</strong></p>
              <pre>{JSON.stringify(d?.intencao ?? {}, null, 1)}</pre>
              <p><strong>Respostas:</strong> {(d?.respostas ?? []).map((r: any) => `${r.tipo}: ${r.status_entrega || r.status}`).join(" · ") || "—"}</p>
            </Estado>
            <button onClick={() => setAberta(null)}>Fechar</button>
          </div>
        </div>
      )}
    </>
  );
}
