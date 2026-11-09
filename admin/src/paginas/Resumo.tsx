import { useQuery } from "@tanstack/react-query";
import { api, ou } from "../api/cliente";
import { Estado, Tabela } from "../componentes/base";

export function Resumo() {
  const q = useQuery({ queryKey: ["resumo"], queryFn: () => ou(api.GET("/api/admin/v1/resumo")), refetchInterval: 30_000 });
  const r = q.data as Record<string, unknown> | undefined;
  const tempos = (r?.tempo_medio_por_etapa ?? {}) as Record<string, number>;
  const cartoes: [string, string][] = [
    ["comerciantes_ativos", "Comerciantes ativos"],
    ["comerciantes_pendentes", "Aguardando aprovação"],
    ["mensagens_24h", "Mensagens (24h)"],
    ["erros_24h", "Erros (24h)"],
    ["pendencias_abertas", "Perguntas em aberto"],
  ];
  return (
    <>
      <h1>Resumo</h1>
      <Estado carregando={q.isLoading} erro={q.error}>
        <div className="cartoes">
          {cartoes.map(([chave, rotulo]) => (
            <div className="cartao" key={chave}>
              <div className="numero">{String(r?.[chave] ?? 0)}</div>
              <div className="rotulo">{rotulo}</div>
            </div>
          ))}
        </div>
        <h2>Tempo médio por etapa (últimas 24h)</h2>
        <Tabela colunas={["Etapa", "Segundos"]} linhas={Object.entries(tempos).map(([e, s]) => [e, s.toFixed(2)])}
                vazio="Sem mensagens nas últimas 24 horas." />
      </Estado>
    </>
  );
}
