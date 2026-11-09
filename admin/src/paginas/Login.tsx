import { useState, type FormEvent } from "react";
import { definirToken, type Sessao } from "../api/cliente";

export function Login({ entrou }: { entrou: (s: Sessao) => void }) {
  const [email, setEmail] = useState("");
  const [senha, setSenha] = useState("");
  const [erro, setErro] = useState("");
  const [enviando, setEnviando] = useState(false);

  async function enviar(e: FormEvent) {
    e.preventDefault();
    setErro("");
    setEnviando(true);
    try {
      const r = await fetch("/api/admin/v1/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, senha }),
      });
      const dados = await r.json().catch(() => ({}));
      if (!r.ok) {
        setErro(typeof dados.detail === "string" ? dados.detail : "Não consegui entrar");
        return;
      }
      definirToken(dados.token);
      entrou({ email: dados.email, papel: dados.papel, trocar_senha: dados.trocar_senha });
    } catch {
      setErro("Sem conexão com a API");
    } finally {
      setEnviando(false);
    }
  }

  return (
    <form className="login" onSubmit={enviar}>
      <h1>Admin do bot</h1>
      <label>
        E-mail
        <input type="email" autoComplete="username" required value={email} onChange={(e) => setEmail(e.target.value)} />
      </label>
      <label>
        Senha
        <input type="password" autoComplete="current-password" required value={senha} onChange={(e) => setSenha(e.target.value)} />
      </label>
      {erro && <p className="erro" role="alert">{erro}</p>}
      <button className="primario" disabled={enviando}>{enviando ? "Entrando…" : "Entrar"}</button>
    </form>
  );
}
