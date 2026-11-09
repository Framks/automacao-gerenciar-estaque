import { useEffect, useState } from "react";
import { NavLink, Navigate, Route, Routes } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { definirToken, quandoSair, renovar, type Sessao } from "./api/cliente";
import { Login } from "./paginas/Login";
import { Resumo } from "./paginas/Resumo";
import { Comerciantes } from "./paginas/Comerciantes";
import { Comerciante } from "./paginas/Comerciante";
import { Mensagens } from "./paginas/Mensagens";
import { Configuracoes } from "./paginas/Configuracoes";
import { Exportacoes } from "./paginas/Exportacoes";
import { TrocarSenha } from "./paginas/TrocarSenha";

function alternarTema() {
  const raiz = document.documentElement;
  const escuro = raiz.dataset.tema ? raiz.dataset.tema === "escuro" : matchMedia("(prefers-color-scheme: dark)").matches;
  raiz.dataset.tema = escuro ? "claro" : "escuro";
}

export function App() {
  const [sessao, setSessao] = useState<Sessao | null | undefined>(undefined);
  const consultas = useQueryClient();

  useEffect(() => {
    quandoSair(() => setSessao(null));
    renovar().then(setSessao);
  }, []);

  if (sessao === undefined) return <p className="suave" style={{ padding: 24 }}>Carregando…</p>;
  if (sessao === null) return <Login entrou={setSessao} />;
  if (sessao.trocar_senha) return <TrocarSenha obrigatoria pronto={() => setSessao({ ...sessao, trocar_senha: false })} />;

  const sair = async () => {
    await fetch("/api/admin/v1/auth/logout", { method: "POST" });
    definirToken(null);
    consultas.clear();
    setSessao(null);
  };

  return (
    <div className="layout">
      <nav className="menu" aria-label="Principal">
        <strong style={{ padding: "0 10px 8px" }}>Admin do bot</strong>
        <NavLink to="/" end>Resumo</NavLink>
        <NavLink to="/comerciantes">Comerciantes</NavLink>
        <NavLink to="/mensagens">Mensagens com erro</NavLink>
        <NavLink to="/configuracoes">Configurações</NavLink>
        <NavLink to="/exportacoes">Exportações</NavLink>
        <div className="rodape">
          <span>{sessao.email} ({sessao.papel})</span>
          <span className="linha">
            <button onClick={alternarTema} aria-label="Alternar tema claro e escuro">Tema</button>
            <NavLink to="/senha">Senha</NavLink>
            <button onClick={sair}>Sair</button>
          </span>
        </div>
      </nav>
      <main>
        <Routes>
          <Route path="/" element={<Resumo />} />
          <Route path="/comerciantes" element={<Comerciantes podeEscrever={sessao.papel === "admin"} />} />
          <Route path="/comerciantes/:id" element={<Comerciante podeEscrever={sessao.papel === "admin"} />} />
          <Route path="/mensagens" element={<Mensagens />} />
          <Route path="/configuracoes" element={<Configuracoes podeEscrever={sessao.papel === "admin"} />} />
          <Route path="/exportacoes" element={<Exportacoes />} />
          <Route path="/senha" element={<TrocarSenha pronto={() => history.back()} />} />
          <Route path="*" element={<Navigate to="/" />} />
        </Routes>
      </main>
    </div>
  );
}
