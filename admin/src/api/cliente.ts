// Cliente da API admin, gerado do OpenAPI (npm run gerar-api). O token de acesso fica só em memória;
// o refresh fica num cookie HttpOnly. Numa resposta 401, tenta o refresh uma vez e repete a chamada.
import createClient from "openapi-fetch";
import type { paths } from "./esquema";

let token: string | null = null;
let aoSair: () => void = () => {};
let renovando: Promise<boolean> | null = null;

export type Sessao = { email: string; papel: string; trocar_senha?: boolean };

export function definirToken(novo: string | null) {
  token = novo;
}

export function quandoSair(fn: () => void) {
  aoSair = fn;
}

export async function renovar(): Promise<Sessao | null> {
  const r = await fetch(`${origem}/api/admin/v1/auth/refresh`, { method: "POST", credentials: "same-origin" });
  if (!r.ok) {
    definirToken(null);
    return null;
  }
  const dados = await r.json();
  definirToken(dados.token);
  return { email: dados.email, papel: dados.papel, trocar_senha: dados.trocar_senha };
}

async function buscar(entrada: Request): Promise<Response> {
  const tentar = (req: Request) => {
    const copia = req.clone();
    if (token) copia.headers.set("Authorization", `Bearer ${token}`);
    return fetch(copia, { credentials: "same-origin" });
  };
  const primeira = await tentar(entrada);
  if (primeira.status !== 401 || entrada.url.includes("/auth/")) return primeira;
  renovando ??= renovar().then((s) => s !== null).finally(() => (renovando = null));
  if (await renovando) return tentar(entrada);
  aoSair();
  return primeira;
}

const origem = typeof location !== "undefined" ? location.origin : "";

export const api = createClient<paths>({ baseUrl: origem, fetch: buscar });

export class ErroApi extends Error {
  constructor(public status: number, mensagem: string) {
    super(mensagem);
  }
}

/** Desembrulha a resposta do openapi-fetch: devolve os dados ou lança ErroApi com a mensagem da API. */
export async function ou<T>(p: Promise<{ data?: T; error?: unknown; response: Response }>): Promise<T> {
  const { data, error, response } = await p;
  if (error !== undefined || !response.ok) {
    const detalhe = (error as { detail?: unknown })?.detail;
    const texto = typeof detalhe === "string" ? detalhe : Array.isArray(detalhe) ? "dados inválidos" : response.statusText;
    throw new ErroApi(response.status, texto || "erro");
  }
  return data as T;
}

/** Download autenticado (CSV/ZIP): busca com o token e salva o arquivo. */
export async function baixar(caminho: string, nome: string) {
  const resposta = await buscar(new Request(origem + caminho));
  if (!resposta.ok) throw new ErroApi(resposta.status, "não consegui baixar");
  const url = URL.createObjectURL(await resposta.blob());
  const a = document.createElement("a");
  a.href = url;
  a.download = nome;
  a.click();
  URL.revokeObjectURL(url);
}
