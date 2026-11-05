"""API admin v1 com o Postgres de teste: autenticação, papéis, recursos, auditoria e isolamento."""

from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

import main
from app.admin import dependencias as dep
from app.admin.auth import gerar_hash
from app.config import get_settings
from app.db.engine import transacao
from app.db.modelos import AdminDB, AuditoriaDB, ComercianteDB, MensagemDB, MovimentoDB, ProdutoDB
from app.fila.controle import MemoriaRedis
from app.servicos.comerciantes import Acesso
from app.servicos.configuracoes import Configuracoes
from app.servicos.exportacao import Exportador, Filtro, juntar

SENHA = "SenhaForte123"


@pytest.fixture
def api(fabrica_pg, monkeypatch):
    monkeypatch.setenv("ADMIN_JWT_SECRET", "x" * 40)
    get_settings.cache_clear()
    redis = MemoriaRedis()
    cfg = Configuracoes(fabrica_pg, redis)
    acesso = Acesso(fabrica_pg, redis)
    main.app.dependency_overrides.update({
        dep.fabrica: lambda: fabrica_pg, dep.redis: lambda: redis, dep.configuracoes: lambda: cfg,
        dep.acesso: lambda: acesso,
    })
    with transacao(fabrica_pg) as s:
        s.add_all([AdminDB(email="admin@x.com", senha_hash=gerar_hash(SENHA), papel="admin", trocar_senha=False),
                   AdminDB(email="leitor@x.com", senha_hash=gerar_hash(SENHA), papel="leitura")])
        a = ComercianteDB(telefone="558881061891", nome="Francisco", status="ativo", permitido=True)
        b = ComercianteDB(telefone="558592572430", nome="Atila", status="ativo", permitido=True)
        p = ComercianteDB(telefone="558899990000", nome="Novo", status="pendente", permitido=False)
        s.add_all([a, b, p])
        s.flush()
        s.add_all([ProdutoDB(comerciante_id=a.id, nome="feijão", chave="feijao", unidade="kg", quantidade=7),
                   ProdutoDB(comerciante_id=b.id, nome="leite", chave="leite", unidade="l", quantidade=0)])
        s.add(MensagemDB(comerciante_id=a.id, direcao="entrada", wa_msg_id="w1", tipo="audio", status="erro",
                         etapa_erro="interpretacao", erro="RateLimit", tempos_json={"transcricao": 1.5}))
        ids = {"a": a.id, "b": b.id, "pendente": p.id}
    cliente = TestClient(main.app, base_url="https://testserver")
    yield cliente, ids, fabrica_pg, redis, acesso
    main.app.dependency_overrides.clear()
    get_settings.cache_clear()


def entrar(cliente, email="admin@x.com", senha=SENHA):
    r = cliente.post("/api/admin/v1/auth/login", json={"email": email, "senha": senha})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


# --- autenticação ---------------------------------------------------------------------------------------

def test_sem_token_tudo_e_401(api):
    c, *_ = api
    for caminho in ("/api/admin/v1/comerciantes", "/api/admin/v1/resumo", "/api/admin/v1/configuracoes",
                    "/api/admin/v1/exportacoes/comerciantes.csv"):
        assert c.get(caminho).status_code == 401
    assert c.get("/api/admin/v1/eu", headers={"Authorization": "Bearer lixo"}).status_code == 401


def test_login_errado_e_limite_de_tentativas(api):
    c, *_ = api
    for _ in range(5):
        assert c.post("/api/admin/v1/auth/login", json={"email": "admin@x.com", "senha": "errada"}).status_code == 401
    assert c.post("/api/admin/v1/auth/login", json={"email": "admin@x.com", "senha": SENHA}).status_code == 429


def test_refresh_rotaciona_e_logout_revoga(api):
    c, *_ = api
    entrar(c)
    r1 = c.post("/api/admin/v1/auth/refresh")
    assert r1.status_code == 200 and r1.json()["papel"] == "admin"
    antigo = r1.cookies.get("admin_refresh") or c.cookies.get("admin_refresh")
    assert c.post("/api/admin/v1/auth/refresh").status_code == 200
    assert TestClient(main.app, base_url="https://testserver",
                      cookies={"admin_refresh": antigo}).post("/api/admin/v1/auth/refresh").status_code == 401
    assert c.post("/api/admin/v1/auth/logout").status_code == 204
    assert c.post("/api/admin/v1/auth/refresh").status_code == 401


def test_cookie_de_refresh_e_seguro(api):
    c, *_ = api
    r = c.post("/api/admin/v1/auth/login", json={"email": "admin@x.com", "senha": SENHA})
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "secure" in cookie and "samesite=strict" in cookie
    assert "path=/api/admin/v1/auth" in cookie


def test_papel_leitura_nao_escreve(api):
    c, ids, *_ = api
    h = entrar(c, "leitor@x.com")
    assert c.get("/api/admin/v1/comerciantes", headers=h).status_code == 200
    assert c.patch(f"/api/admin/v1/comerciantes/{ids['pendente']}", json={"permitido": True},
                   headers=h).status_code == 403
    assert c.put("/api/admin/v1/flags/despesas", json={"ligado": False}, headers=h).status_code == 403


def test_trocar_senha(api):
    c, *_ = api
    h = entrar(c)
    assert c.post("/api/admin/v1/auth/senha", json={"senha_atual": SENHA, "senha_nova": "curta"},
                  headers=h).status_code == 422
    assert c.post("/api/admin/v1/auth/senha", json={"senha_atual": SENHA, "senha_nova": "NovaSenhaBoa456"},
                  headers=h).status_code == 204
    entrar(c, senha="NovaSenhaBoa456")


# --- comerciantes ---------------------------------------------------------------------------------------

def test_listar_filtrar_e_paginar(api):
    c, ids, *_ = api
    h = entrar(c)
    r = c.get("/api/admin/v1/comerciantes?status=pendente", headers=h).json()
    assert [x["telefone"] for x in r["itens"]] == ["558899990000"]
    p1 = c.get("/api/admin/v1/comerciantes?limite=2", headers=h).json()
    assert len(p1["itens"]) == 2 and p1["proximo"]
    p2 = c.get(f"/api/admin/v1/comerciantes?limite=2&depois={p1['proximo']}", headers=h).json()
    assert len(p2["itens"]) == 1 and p2["proximo"] is None
    assert c.get("/api/admin/v1/comerciantes?busca=Franc", headers=h).json()["itens"][0]["produtos"] == 1


def test_aprovar_pendente_libera_o_bot(api):
    c, ids, fabrica, redis, acesso = api
    assert acesso.verificar("558899990000") == "pendente"  # o webhook já tinha guardado em cache
    h = entrar(c)
    r = c.patch(f"/api/admin/v1/comerciantes/{ids['pendente']}", json={"permitido": True}, headers=h)
    assert r.status_code == 200 and r.json()["status"] == "ativo"
    assert acesso.verificar("558899990000") == "permitido"  # cache limpo pela aprovação
    with transacao(fabrica) as s:
        a = s.scalar(select(AuditoriaDB).where(AuditoriaDB.acao == "editar"))
        assert a.admin_email == "admin@x.com" and a.diff_json["permitido"] == [False, True]


def test_pre_aprovar_numero_com_nono_digito(api):
    c, _, _, _, acesso = api
    h = entrar(c)
    r = c.post("/api/admin/v1/comerciantes", json={"telefone": "+55 85 98888-7777", "nome": "Maria"}, headers=h)
    assert r.status_code == 201 and r.json()["telefone"] == "558588887777"
    assert acesso.verificar("558588887777") == "permitido"


# --- estoque e livro-caixa ------------------------------------------------------------------------------

def test_ajuste_de_estoque_gera_movimento_com_autor_e_auditoria(api):
    c, ids, fabrica, *_ = api
    h = entrar(c)
    (produto,) = c.get(f"/api/admin/v1/comerciantes/{ids['a']}/produtos", headers=h).json()
    r = c.post(f"/api/admin/v1/comerciantes/{ids['a']}/produtos/{produto['id']}/ajustes",
               json={"motivo": "contagem física", "quantidade_nova": "6.5", "custo_medio_novo": 10}, headers=h)
    assert r.status_code == 201 and r.json()["quantidade"] == 6.5 and r.json()["custo_medio"] == 10
    with transacao(fabrica) as s:
        movs = s.scalars(select(MovimentoDB).order_by(MovimentoDB.id)).all()
        assert [(m.tipo, m.quantidade, m.autor, m.motivo) for m in movs] == [
            ("ajuste", Decimal("-0.500"), "admin@x.com", "contagem física"),
            ("ajuste_valor", Decimal("0.000"), "admin@x.com", "contagem física")]
        assert s.scalar(select(AuditoriaDB.acao).where(AuditoriaDB.entidade == "produto")) == "ajustar_estoque"
    assert c.post(f"/api/admin/v1/comerciantes/{ids['a']}/produtos/{produto['id']}/ajustes",
                  json={"motivo": "sem nada"}, headers=h).status_code == 422
    listados = c.get(f"/api/admin/v1/comerciantes/{ids['a']}/movimentos", headers=h).json()["itens"]
    assert [m["tipo"] for m in listados] == ["ajuste_valor", "ajuste"]


def test_produto_de_outro_comerciante_nao_vaza(api):
    c, ids, *_ = api
    h = entrar(c)
    (leite,) = c.get(f"/api/admin/v1/comerciantes/{ids['b']}/produtos", headers=h).json()
    r = c.post(f"/api/admin/v1/comerciantes/{ids['a']}/produtos/{leite['id']}/ajustes",
               json={"motivo": "tentativa", "quantidade_nova": 100}, headers=h)
    assert r.status_code == 404
    assert c.get("/api/admin/v1/comerciantes/9999/produtos", headers=h).status_code == 404


def test_filtro_com_injecao_nao_passa(api):
    c, ids, *_ = api
    h = entrar(c)
    r = c.get("/api/admin/v1/comerciantes", params={"busca": "' OR 1=1 --"}, headers=h)
    assert r.status_code == 200 and r.json()["itens"] == []
    assert c.get(f"/api/admin/v1/comerciantes/{ids['a']}/movimentos?tipo=x' or '1",
                 headers=h).status_code == 422


# --- mensagens, configurações, exportação ---------------------------------------------------------------

def test_mensagens_com_erro(api):
    c, *_ = api
    h = entrar(c)
    (m,) = c.get("/api/admin/v1/mensagens?status=erro", headers=h).json()["itens"]
    assert (m["etapa_erro"], m["erro"]) == ("interpretacao", "RateLimit")
    assert c.get(f"/api/admin/v1/mensagens/{m['id']}", headers=h).json()["respostas"] == []


def test_configuracao_e_flag(api):
    c, ids, *_ = api
    h = entrar(c)
    assert c.put("/api/admin/v1/configuracoes/tts_voz", json={"valor": "pt-BR-AntonioNeural"},
                 headers=h).status_code == 200
    assert c.put("/api/admin/v1/configuracoes/tts_voz", json={"valor": "voz-falsa"}, headers=h).status_code == 422
    assert c.put(f"/api/admin/v1/flags/resposta_em_audio?comerciante_id={ids['a']}", json={"ligado": False},
                 headers=h).status_code == 200
    tudo = c.get("/api/admin/v1/configuracoes", headers=h).json()
    voz = next(x for x in tudo["configuracoes"] if x["chave"] == "tts_voz")
    audio = next(x for x in tudo["flags"] if x["chave"] == "resposta_em_audio")
    assert voz["global"] == "pt-BR-AntonioNeural" and audio["comerciantes"] == {str(ids["a"]): False}
    assert c.delete("/api/admin/v1/configuracoes/tts_voz", headers=h).status_code == 204


def test_exportacao_csv_igual_ao_servico_e_zip(api):
    c, ids, fabrica, *_ = api
    h = entrar(c)
    r = c.get(f"/api/admin/v1/exportacoes/estoque.csv?comerciante_id={ids['a']}", headers=h)
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    assert r.content.decode("utf-8") == juntar(Exportador(fabrica).csv("estoque", Filtro(comerciante_id=ids["a"])))
    assert c.get("/api/admin/v1/exportacoes/movimentos.csv", headers=h).status_code == 422
    assert c.get("/api/admin/v1/exportacoes/senhas.csv", headers=h).status_code == 404
    z = c.get(f"/api/admin/v1/comerciantes/{ids['a']}/exportacao.zip", headers=h)
    assert z.status_code == 200 and z.content[:2] == b"PK"


def test_resumo_saude_auditoria_e_openapi(api):
    c, *_ = api
    h = entrar(c)
    r = c.get("/api/admin/v1/resumo", headers=h).json()
    assert (r["comerciantes_ativos"], r["comerciantes_pendentes"], r["erros_24h"]) == (2, 1, 1)
    assert r["tempo_medio_por_etapa"] == {"transcricao": 1.5}
    assert c.get("/api/admin/v1/saude", headers=h).json()["banco"] == "ok"
    assert c.get("/api/admin/v1/auditoria", headers=h).json()["itens"][0]["acao"] == "login"
    esquema = c.get("/api/admin/v1/openapi.json").json()
    assert "/api/admin/v1/comerciantes" in esquema["paths"]
