import { useState, type FormEvent } from "react";

export function TrocarSenha({ obrigatoria = false, pronto }: { obrigatoria?: boolean; pronto: () => void }) {
  const [atual, setAtual] = useState("");
  const [nova, setNova] = useState("");
  const [erro, setErro] = useState("");

  async function enviar(e: FormEvent) {
    e.preventDefault();
    const { api, ou } = await import("../api/cliente");
    try {
      await ou(api.POST("/api/admin/v1/auth/senha", { body: { senha_atual: atual, senha_nova: nova } }));
      pronto();
    } catch (err) {
      setErro(err instanceof Error ? err.message : "Não consegui trocar a senha");
    }
  }

  return (
    <form className="login" onSubmit={enviar}>
      <h1>Trocar senha</h1>
      {obrigatoria && <p className="suave">Antes de continuar, troque a senha provisória.</p>}
      <label>
        Senha atual
        <input type="password" autoComplete="current-password" required value={atual} onChange={(e) => setAtual(e.target.value)} />
      </label>
      <label>
        Senha nova (12 ou mais caracteres, com maiúsculas, minúsculas e números)
        <input type="password" autoComplete="new-password" required minLength={12} value={nova} onChange={(e) => setNova(e.target.value)} />
      </label>
      {erro && <p className="erro" role="alert">{erro}</p>}
      <button className="primario">Salvar</button>
    </form>
  );
}
