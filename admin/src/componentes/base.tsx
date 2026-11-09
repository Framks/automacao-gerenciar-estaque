import type { ReactNode } from "react";

export function Selo({ valor }: { valor: string | null | undefined }) {
  if (!valor) return <span className="suave">—</span>;
  return <span className={`selo ${valor}`}>{valor}</span>;
}

export function Estado({ carregando, erro, children }: { carregando: boolean; erro: unknown; children: ReactNode }) {
  if (carregando) return <p className="suave" role="status">Carregando…</p>;
  if (erro) return <p className="erro" role="alert">{erro instanceof Error ? erro.message : "Erro ao carregar"}</p>;
  return <>{children}</>;
}

export function Abas<T extends string>({ abas, atual, mudar }: { abas: [T, string][]; atual: T; mudar: (t: T) => void }) {
  return (
    <div className="abas" role="tablist">
      {abas.map(([id, rotulo]) => (
        <button key={id} role="tab" aria-selected={atual === id} onClick={() => mudar(id)}>
          {rotulo}
        </button>
      ))}
    </div>
  );
}

export function Tabela({ colunas, linhas, vazio = "Nada por aqui." }: { colunas: string[]; linhas: ReactNode[][]; vazio?: string }) {
  if (!linhas.length) return <p className="suave">{vazio}</p>;
  return (
    <div className="tabela">
      <table>
        <thead>
          <tr>{colunas.map((c) => <th key={c} scope="col">{c}</th>)}</tr>
        </thead>
        <tbody>
          {linhas.map((l, i) => (
            <tr key={i}>{l.map((c, j) => <td key={j}>{c}</td>)}</tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function data(valor: string | null | undefined) {
  if (!valor) return "—";
  return new Date(valor).toLocaleString("pt-BR", { dateStyle: "short", timeStyle: "short" });
}

export function num(valor: number | null | undefined, casas = 3) {
  if (valor === null || valor === undefined) return "—";
  return valor.toLocaleString("pt-BR", { maximumFractionDigits: casas });
}

export function reais(valor: number | null | undefined) {
  if (valor === null || valor === undefined) return "—";
  return valor.toLocaleString("pt-BR", { style: "currency", currency: "BRL" });
}
