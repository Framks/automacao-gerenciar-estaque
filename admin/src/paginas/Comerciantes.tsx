import { useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ou } from "../api/cliente";
import { Abas, Estado, Selo, Tabela, data } from "../componentes/base";

type Status = "pendente" | "ativo" | "bloqueado";
type Comerciante = { id: number; telefone: string; nome: string; status: string; permitido: boolean; criado_em: string; produtos: number; pendencias_abertas: number };

export function Comerciantes({ podeEscrever }: { podeEscrever: boolean }) {
  const [status, setStatus] = useState<Status>("ativo");
  const [busca, setBusca] = useState("");
  const [novo, setNovo] = useState("");
  const consultas = useQueryClient();
  const q = useQuery({
    queryKey: ["comerciantes", status, busca],
    queryFn: () => ou(api.GET("/api/admin/v1/comerciantes", { params: { query: { status, busca, limite: 200 } } })),
  });
  const editar = useMutation({
    mutationFn: ({ id, corpo }: { id: number; corpo: { status?: Status; permitido?: boolean } }) =>
      ou(api.PATCH("/api/admin/v1/comerciantes/{comerciante_id}", { params: { path: { comerciante_id: id } }, body: corpo })),
    onSuccess: () => consultas.invalidateQueries({ queryKey: ["comerciantes"] }),
  });
  const preAprovar = useMutation({
    mutationFn: (telefone: string) => ou(api.POST("/api/admin/v1/comerciantes", { body: { telefone, nome: "" } })),
    onSuccess: () => {
      setNovo("");
      consultas.invalidateQueries({ queryKey: ["comerciantes"] });
    },
  });
  const itens = ((q.data as { itens?: Comerciante[] } | undefined)?.itens ?? []);

  return (
    <>
      <h1>Comerciantes</h1>
      <Abas<Status> abas={[["pendente", "Pendentes de aprovação"], ["ativo", "Ativos"], ["bloqueado", "Bloqueados"]]} atual={status} mudar={setStatus} />
      <div className="linha" style={{ marginBottom: 12 }}>
        <label>
          Buscar (nome ou telefone)
          <input value={busca} onChange={(e) => setBusca(e.target.value)} />
        </label>
        {podeEscrever && (
          <form className="linha" onSubmit={(e) => { e.preventDefault(); preAprovar.mutate(novo); }}>
            <label>
              Pré-aprovar número (com DDI)
              <input value={novo} onChange={(e) => setNovo(e.target.value)} placeholder="5585999990000" inputMode="tel" />
            </label>
            <button className="primario" disabled={!novo || preAprovar.isPending}>Pré-aprovar</button>
          </form>
        )}
      </div>
      {(editar.error || preAprovar.error) && <p className="erro" role="alert">{String((editar.error ?? preAprovar.error)?.message)}</p>}
      <Estado carregando={q.isLoading} erro={q.error}>
        <Tabela
          colunas={["Nome", "Telefone", "Status", "Produtos", "Perguntas abertas", "Desde", ""]}
          vazio={status === "pendente" ? "Nenhum número esperando aprovação." : "Nenhum comerciante."}
          linhas={itens.map((c) => [
            <Link to={`/comerciantes/${c.id}`}>{c.nome || "(sem nome)"}</Link>,
            c.telefone,
            <Selo valor={c.status} />,
            c.produtos,
            c.pendencias_abertas,
            data(c.criado_em),
            podeEscrever ? (
              <span className="linha">
                {c.status !== "ativo" && (
                  <button className="primario" onClick={() => editar.mutate({ id: c.id, corpo: { status: "ativo", permitido: true } })}>
                    {c.status === "pendente" ? "Aprovar" : "Desbloquear"}
                  </button>
                )}
                {c.status !== "bloqueado" && (
                  <button className="perigo" onClick={() => editar.mutate({ id: c.id, corpo: { status: "bloqueado" } })}>Bloquear</button>
                )}
              </span>
            ) : null,
          ])}
        />
      </Estado>
    </>
  );
}
