import type { ReactNode } from "react";
import { render } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

export type Chamada = { metodo: string; url: string; corpo: unknown; auth: string | null };

/** Simula a API: `rotas` mapeia "METODO /caminho" (sem query) para [status, corpo]. Guarda as chamadas. */
export function simularApi(rotas: Record<string, [number, unknown] | ((c: Chamada) => [number, unknown])>) {
  const chamadas: Chamada[] = [];
  globalThis.fetch = (async (entrada: RequestInfo | URL, init?: RequestInit) => {
    const req = entrada instanceof Request ? entrada : new Request(new URL(String(entrada), "http://localhost"), init);
    const url = new URL(req.url, "http://localhost");
    const texto = req.method === "GET" ? "" : await req.clone().text();
    const chamada = { metodo: req.method, url: url.pathname + url.search, corpo: texto ? JSON.parse(texto) : null, auth: req.headers.get("Authorization") };
    chamadas.push(chamada);
    const rota = rotas[`${req.method} ${url.pathname}`];
    const [status, corpo] = typeof rota === "function" ? rota(chamada) : rota ?? [404, { detail: "não simulado" }];
    return new Response(status === 204 ? null : JSON.stringify(corpo), { status, headers: { "Content-Type": "application/json" } });
  }) as typeof fetch;
  return chamadas;
}

export function montar(ui: ReactNode, rota = "/") {
  const cliente = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={cliente}>
      <MemoryRouter initialEntries={[rota]}>{ui}</MemoryRouter>
    </QueryClientProvider>,
  );
}
