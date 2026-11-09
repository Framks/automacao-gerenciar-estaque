import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, baixar, ou } from "../api/cliente";

const TIPOS: [string, string, boolean][] = [
  ["comerciantes", "Comerciantes", false], ["estoque", "Estoque", false], ["movimentos", "Movimentos", true],
  ["caixa", "Caixa", true], ["pendencias", "Perguntas (pendências)", false], ["mensagens", "Mensagens", true],
  ["configuracoes", "Configurações", false], ["feature_flags", "Funções (flags)", false],
];

export function Exportacoes() {
  const [tipo, setTipo] = useState("estoque");
  const [comerciante, setComerciante] = useState("");
  const [de, setDe] = useState("");
  const [ate, setAte] = useState("");
  const [erro, setErro] = useState("");
  const lista = useQuery({ queryKey: ["comerciantes", "todos"], queryFn: () => ou(api.GET("/api/admin/v1/comerciantes", { params: { query: { limite: 200 } } })) });
  const comerciantes = ((lista.data as { itens?: { id: number; nome: string; telefone: string }[] } | undefined)?.itens ?? []);
  const exige = TIPOS.find(([t]) => t === tipo)?.[2] ?? false;

  async function baixarCsv() {
    setErro("");
    const q = new URLSearchParams();
    if (comerciante) q.set("comerciante_id", comerciante);
    if (de) q.set("de", de);
    if (ate) q.set("ate", ate);
    try {
      await baixar(`/api/admin/v1/exportacoes/${tipo}.csv?${q}`, `${tipo}${comerciante ? "_" + comerciante : ""}.csv`);
    } catch (e) {
      setErro(e instanceof Error ? e.message : "Não consegui baixar");
    }
  }

  return (
    <>
      <h1>Exportações (CSV)</h1>
      <p className="suave">Arquivos prontos para o Excel em português: separador ponto e vírgula, vírgula decimal.</p>
      <div className="linha">
        <label>Tipo
          <select value={tipo} onChange={(e) => setTipo(e.target.value)}>
            {TIPOS.map(([t, r]) => <option key={t} value={t}>{r}</option>)}
          </select>
        </label>
        <label>Comerciante{exige ? " (obrigatório)" : ""}
          <select value={comerciante} onChange={(e) => setComerciante(e.target.value)}>
            <option value="">Todos</option>
            {comerciantes.map((c) => <option key={c.id} value={c.id}>{c.nome || c.telefone}</option>)}
          </select>
        </label>
        <label>De<input type="date" value={de} onChange={(e) => setDe(e.target.value)} /></label>
        <label>Até<input type="date" value={ate} onChange={(e) => setAte(e.target.value)} /></label>
        <button className="primario" onClick={baixarCsv} disabled={exige && !comerciante}>Baixar CSV</button>
      </div>
      {erro && <p className="erro" role="alert">{erro}</p>}
    </>
  );
}
