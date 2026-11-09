import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Routes, Route } from "react-router-dom";
import { definirToken, api, ou } from "../src/api/cliente";
import { Login } from "../src/paginas/Login";
import { Comerciantes } from "../src/paginas/Comerciantes";
import { Configuracoes } from "../src/paginas/Configuracoes";
import { Comerciante } from "../src/paginas/Comerciante";
import { montar, simularApi } from "./ajuda";

beforeEach(() => definirToken("tok-1"));

test("login envia e-mail e senha e entrega a sessão", async () => {
  const chamadas = simularApi({ "POST /api/admin/v1/auth/login": [200, { token: "t", email: "a@x.com", papel: "admin", trocar_senha: false, expira_em_segundos: 900 }] });
  const entrou = vi.fn();
  montar(<Login entrou={entrou} />);
  await userEvent.type(screen.getByLabelText("E-mail"), "a@x.com");
  await userEvent.type(screen.getByLabelText("Senha"), "segredo");
  await userEvent.click(screen.getByRole("button", { name: "Entrar" }));
  await waitFor(() => expect(entrou).toHaveBeenCalledWith({ email: "a@x.com", papel: "admin", trocar_senha: false }));
  expect(chamadas[0].corpo).toEqual({ email: "a@x.com", senha: "segredo" });
});

test("login com erro mostra a mensagem da API", async () => {
  simularApi({ "POST /api/admin/v1/auth/login": [401, { detail: "e-mail ou senha incorretos" }] });
  montar(<Login entrou={() => {}} />);
  await userEvent.type(screen.getByLabelText("E-mail"), "a@x.com");
  await userEvent.type(screen.getByLabelText("Senha"), "x");
  await userEvent.click(screen.getByRole("button", { name: "Entrar" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("e-mail ou senha incorretos");
});

test("aprovar um número pendente chama o PATCH com o token", async () => {
  const pendente = { id: 7, telefone: "558899990000", nome: "", status: "pendente", permitido: false, criado_em: "2026-11-04T10:00:00Z", produtos: 0, pendencias_abertas: 0 };
  const chamadas = simularApi({
    "GET /api/admin/v1/comerciantes": [200, { itens: [pendente], proximo: null }],
    "PATCH /api/admin/v1/comerciantes/7": [200, { ...pendente, status: "ativo", permitido: true }],
  });
  montar(<Comerciantes podeEscrever />);
  await userEvent.click(screen.getByRole("tab", { name: "Pendentes de aprovação" }));
  await userEvent.click(await screen.findByRole("button", { name: "Aprovar" }));
  await waitFor(() => expect(chamadas.some((c) => c.metodo === "PATCH")).toBe(true));
  const patch = chamadas.find((c) => c.metodo === "PATCH")!;
  expect(patch.corpo).toEqual({ status: "ativo", permitido: true });
  expect(patch.auth).toBe("Bearer tok-1");
});

test("papel leitura não vê botões de escrita", async () => {
  simularApi({ "GET /api/admin/v1/comerciantes": [200, { itens: [{ id: 1, telefone: "5588", nome: "Zé", status: "ativo", permitido: true, criado_em: "2026-11-04T10:00:00Z", produtos: 1, pendencias_abertas: 0 }], proximo: null }] });
  montar(<Comerciantes podeEscrever={false} />);
  expect(await screen.findByText("Zé")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Bloquear" })).toBeNull();
});

test("desligar uma flag para um comerciante", async () => {
  const chamadas = simularApi({
    "GET /api/admin/v1/configuracoes": [200, { versao: "3", configuracoes: [], flags: [{ chave: "resposta_em_audio", descricao: "Responder em áudio", padrao: true, global: null, comerciantes: {} }] }],
    "GET /api/admin/v1/comerciantes": [200, { itens: [{ id: 5, nome: "Francisco", telefone: "5588" }], proximo: null }],
    "PUT /api/admin/v1/flags/resposta_em_audio": [200, { chave: "resposta_em_audio", ligado: false, comerciante_id: 5 }],
  });
  montar(<Configuracoes podeEscrever />);
  await screen.findByRole("option", { name: "Francisco" });
  await userEvent.selectOptions(screen.getByLabelText("Escopo"), "5");
  await userEvent.click(await screen.findByLabelText("Ligar resposta_em_audio"));
  await waitFor(() => expect(chamadas.some((c) => c.metodo === "PUT")).toBe(true));
  const put = chamadas.find((c) => c.metodo === "PUT")!;
  expect(put.url).toBe("/api/admin/v1/flags/resposta_em_audio?comerciante_id=5");
  expect(put.corpo).toEqual({ ligado: false });
});

test("ajuste de estoque pede motivo e envia só o que mudou", async () => {
  const chamadas = simularApi({
    "GET /api/admin/v1/comerciantes/1": [200, { id: 1, telefone: "5588", nome: "Francisco", status: "ativo" }],
    "GET /api/admin/v1/comerciantes/1/produtos": [200, [{ id: 9, produto: "feijão", unidade: "kg", quantidade: 7, custo_medio: null, atualizado_em: "2026-11-04T10:00:00Z" }]],
    "POST /api/admin/v1/comerciantes/1/produtos/9/ajustes": [201, { produto_id: 9, quantidade: 6.5, custo_medio: 10, movimentos: [1, 2] }],
  });
  montar(<Routes><Route path="/comerciantes/:id" element={<Comerciante podeEscrever />} /></Routes>, "/comerciantes/1");
  await userEvent.click(await screen.findByRole("button", { name: "Ajustar" }));
  const quantidade = screen.getByLabelText("Quantidade (kg)");
  await userEvent.clear(quantidade);
  await userEvent.type(quantidade, "6,5");
  await userEvent.type(screen.getByLabelText("Custo médio (R$ por kg)"), "10");
  await userEvent.type(screen.getByLabelText("Motivo"), "contagem");
  await userEvent.click(screen.getByRole("button", { name: "Salvar ajuste" }));
  await waitFor(() => expect(chamadas.some((c) => c.metodo === "POST")).toBe(true));
  expect(chamadas.find((c) => c.metodo === "POST")!.corpo).toEqual({ motivo: "contagem", quantidade_nova: "6.5", custo_medio_novo: "10" });
});

test("401 renova o token uma vez e repete a chamada", async () => {
  let n = 0;
  const chamadas = simularApi({
    "GET /api/admin/v1/resumo": (c) => (c.auth === "Bearer novo" ? [200, { erros_24h: 0 }] : [401, { detail: "expirado" }]),
    "POST /api/admin/v1/auth/refresh": () => (n++, [200, { token: "novo", email: "a@x.com", papel: "admin" }]),
  });
  const r = await ou(api.GET("/api/admin/v1/resumo"));
  expect(r).toEqual({ erros_24h: 0 });
  expect(n).toBe(1);
  expect(chamadas.map((c) => c.metodo + " " + c.url)).toEqual(["GET /api/admin/v1/resumo", "POST /api/admin/v1/auth/refresh", "GET /api/admin/v1/resumo"]);
});

test("relatório do caixa mostra os totais por meio e os dias com movimento", async () => {
  simularApi({
    "GET /api/admin/v1/comerciantes/1": [200, { id: 1, telefone: "5588", nome: "Francisco", status: "ativo" }],
    "GET /api/admin/v1/comerciantes/1/produtos": [200, []],
    "GET /api/admin/v1/comerciantes/1/relatorios/caixa": [200, {
      periodo: { inicio: "2026-11-01", fim: "2026-11-08", rotulo: "novembro (até 08/11)" },
      vendas: "21.00", por_forma: { pix: "14.00", fiado: "7.00" }, compras: "50.00", despesas: "0.00", despesas_por_categoria: {},
      retiradas: "0.00", recebido_fiado: "0.00", vendido_fiado: "7.00", entrou: "14.00", saiu: "50.00", saldo: "-36.00", lucro_bruto: "6.00",
      avisos: { compras_sem_valor: 1, vendas_sem_valor: 0, vendas_sem_custo: 0 },
      dias: [{ dia: "2026-11-02", vendas: "21.00", compras: "50.00", despesas: "0.00", retiradas: "0.00", vendido_fiado: "7.00", recebido_fiado: "0.00", entrou: "14.00", saiu: "50.00", saldo: "-36.00" },
             { dia: "2026-11-03", vendas: "0.00", compras: "0.00", despesas: "0.00", retiradas: "0.00", vendido_fiado: "0.00", recebido_fiado: "0.00", entrou: "0.00", saiu: "0.00", saldo: "0.00" }],
      itens: [{ produto: "arroz", tipo: "venda", unidade: "kg", quantidade: "3.000", valor: "21.00", sem_valor: 0, formas: ["pix", "fiado"] }],
    }],
  });
  montar(<Routes><Route path="/comerciantes/:id" element={<Comerciante podeEscrever />} /></Routes>, "/comerciantes/1");
  await userEvent.click(await screen.findByRole("tab", { name: "Relatório" }));
  expect(await screen.findByText("novembro (até 08/11)")).toBeInTheDocument();
  expect(screen.getByText("Pix, Fiado")).toBeInTheDocument();
  expect(screen.getByText("1 compras sem valor")).toBeInTheDocument();
  expect(screen.getByText("02/11")).toBeInTheDocument();
  expect(screen.queryByText("03/11")).toBeNull();
});
