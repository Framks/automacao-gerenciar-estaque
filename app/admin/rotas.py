"""API admin v1 (/api/admin/v1). As rotas só traduzem HTTP; a regra fica em app/servicos e app/domain.

Toda escrita exige o papel `admin` e gera uma linha de auditoria. Listas usam cursor (?limite=&depois=<id>).
"""

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.admin import dependencias as dep
from app.admin.auth import (
    CAMINHO_COOKIE,
    COOKIE_REFRESH,
    REFRESH_SEGUNDOS,
    AdminToken,
    TokenInvalido,
    gerar_hash,
    senha_confere,
    senha_forte,
)
from app.db.modelos import (
    AdminDB,
    AuditoriaDB,
    ClienteDB,
    ComercianteDB,
    FechamentoDB,
    FiadoLancamentoDB,
    LancamentoDB,
    MensagemDB,
    MovimentoDB,
    PendenciaDB,
    ProdutoDB,
)
from app.servicos.configuracoes import ConfiguracaoInvalida
from app.servicos.estoque import AjusteInvalido, ajustar_produto, auditar
from app.servicos.exportacao import EXPORTACOES, ExportacaoInvalida, Exportador, Filtro
from app.whatsapp.client import mascarar, wa_id_brasil

router = APIRouter(prefix="/api/admin/v1")
LIMITE_PADRAO, LIMITE_MAXIMO = 50, 200


# --- esquemas -------------------------------------------------------------------------------------------

class Login(BaseModel):
    email: str = Field(min_length=3, max_length=200)
    senha: str = Field(min_length=1, max_length=200)


class TokenSaida(BaseModel):
    token: str
    expira_em_segundos: int
    email: str
    papel: str
    trocar_senha: bool = False


class TrocarSenha(BaseModel):
    senha_atual: str
    senha_nova: str


class Pagina(BaseModel):
    itens: list[dict[str, Any]]
    proximo: int | None = None


class ComercianteSaida(BaseModel):
    id: int
    telefone: str
    nome: str
    status: str
    permitido: bool
    criado_em: datetime
    produtos: int = 0
    pendencias_abertas: int = 0


class ComercianteNovo(BaseModel):
    telefone: str = Field(min_length=10, max_length=20, pattern=r"^\+?[0-9 ()-]+$")
    nome: str = ""


class ComercianteEdicao(BaseModel):
    nome: str | None = None
    status: Literal["ativo", "bloqueado", "pendente"] | None = None
    permitido: bool | None = None


class Ajuste(BaseModel):
    motivo: str = Field(min_length=3, max_length=500)
    quantidade_nova: Decimal | None = None
    custo_medio_novo: Decimal | None = None


class ValorConfig(BaseModel):
    valor: Any


class ValorFlag(BaseModel):
    ligado: bool


# --- apoio -----------------------------------------------------------------------------------------------

def _limite(limite: int) -> int:
    return max(1, min(limite, LIMITE_MAXIMO))


def _pagina(linhas: list, limite: int, para_dict) -> Pagina:
    itens = [para_dict(l) for l in linhas[:limite]]
    proximo = linhas[limite - 1].id if len(linhas) > limite else None
    return Pagina(itens=itens, proximo=proximo)


def _comerciante(s: Session, comerciante_id: int) -> ComercianteDB:
    c = s.get(ComercianteDB, comerciante_id)
    if c is None:
        raise HTTPException(404, "comerciante não encontrado")
    return c


def _num(v):
    return None if v is None else float(v)


def _cookie_refresh(resposta: Response, valor: str | None) -> None:
    if valor is None:
        resposta.delete_cookie(COOKIE_REFRESH, path=CAMINHO_COOKIE, secure=True, httponly=True, samesite="strict")
    else:
        resposta.set_cookie(COOKIE_REFRESH, valor, max_age=REFRESH_SEGUNDOS, path=CAMINHO_COOKIE, secure=True,
                            httponly=True, samesite="strict")


# --- autenticação ---------------------------------------------------------------------------------------

@router.post("/auth/login", response_model=TokenSaida, tags=["auth"])
def login(dados: Login, request: Request, response: Response, s: Session = Depends(dep.sessao),
          t=Depends(dep.tokens), limite=Depends(dep.limite)):
    ip, email = dep.ip_do_cliente(request), dados.email.strip().lower()
    if limite.bloqueado(ip, email):
        raise HTTPException(429, "muitas tentativas; espere 15 minutos")
    admin = s.scalar(select(AdminDB).where(func.lower(AdminDB.email) == email))
    if admin is None or not admin.ativo or not senha_confere(admin.senha_hash, dados.senha):
        limite.falhou(ip, email)
        raise HTTPException(401, "e-mail ou senha incorretos")
    limite.acertou(ip, email)
    token = AdminToken(admin.id, admin.email, admin.papel)
    _cookie_refresh(response, t.refresh(token))
    auditar(s, admin.id, admin.email, "login", "admin", admin.id)
    return TokenSaida(token=t.acesso(token), expira_em_segundos=15 * 60, email=admin.email, papel=admin.papel,
                      trocar_senha=admin.trocar_senha)


@router.post("/auth/refresh", response_model=TokenSaida, tags=["auth"])
def refresh(request: Request, response: Response, s: Session = Depends(dep.sessao), t=Depends(dep.tokens)):
    bruto = request.cookies.get(COOKIE_REFRESH)
    if not bruto:
        raise HTTPException(401, "faça login")
    try:
        token, dados = t.ler(bruto, "refresh")
    except TokenInvalido:
        _cookie_refresh(response, None)
        raise HTTPException(401, "sessão expirada") from None
    admin = s.get(AdminDB, token.id)
    if admin is None or not admin.ativo:
        raise HTTPException(401, "acesso removido")
    t.revogar(dados)  # rotação: cada refresh só vale uma vez
    atual = AdminToken(admin.id, admin.email, admin.papel)
    _cookie_refresh(response, t.refresh(atual))
    return TokenSaida(token=t.acesso(atual), expira_em_segundos=15 * 60, email=admin.email, papel=admin.papel,
                      trocar_senha=admin.trocar_senha)


@router.post("/auth/logout", status_code=204, tags=["auth"])
def logout(request: Request, response: Response, t=Depends(dep.tokens)):
    bruto = request.cookies.get(COOKIE_REFRESH)
    if bruto:
        try:
            _, dados = t.ler(bruto, "refresh")
            t.revogar(dados)
        except TokenInvalido:
            pass
    _cookie_refresh(response, None)
    response.status_code = 204
    return response


@router.post("/auth/senha", status_code=204, tags=["auth"])
def trocar_senha(dados: TrocarSenha, admin: AdminToken = Depends(dep.admin_atual), s: Session = Depends(dep.sessao)):
    linha = s.get(AdminDB, admin.id)
    if linha is None or not senha_confere(linha.senha_hash, dados.senha_atual):
        raise HTTPException(400, "senha atual incorreta")
    motivo = senha_forte(dados.senha_nova)
    if motivo:
        raise HTTPException(422, motivo)
    linha.senha_hash, linha.trocar_senha = gerar_hash(dados.senha_nova), False
    auditar(s, admin.id, admin.email, "trocar_senha", "admin", admin.id)
    return Response(status_code=204)


@router.get("/eu", tags=["auth"])
def eu(admin: AdminToken = Depends(dep.admin_atual)):
    return {"id": admin.id, "email": admin.email, "papel": admin.papel}


# --- comerciantes ---------------------------------------------------------------------------------------

def _comerciante_saida(c: ComercianteDB, produtos: int = 0, pendencias: int = 0) -> dict:
    return ComercianteSaida(id=c.id, telefone=c.telefone, nome=c.nome, status=c.status, permitido=c.permitido,
                            criado_em=c.criado_em, produtos=produtos, pendencias_abertas=pendencias).model_dump()


@router.get("/comerciantes", response_model=Pagina, tags=["comerciantes"])
def listar_comerciantes(busca: str = "", status: Literal["ativo", "bloqueado", "pendente"] | None = None,
                        limite: int = LIMITE_PADRAO, depois: int | None = None,
                        _: AdminToken = Depends(dep.admin_atual), s: Session = Depends(dep.sessao)):
    limite = _limite(limite)
    q = select(ComercianteDB).order_by(ComercianteDB.id.desc())
    if busca.strip():
        termo = f"%{busca.strip()}%"
        q = q.where(ComercianteDB.nome.ilike(termo) | ComercianteDB.telefone.like(termo))
    if status:
        q = q.where(ComercianteDB.status == status)
    if depois:
        q = q.where(ComercianteDB.id < depois)
    linhas = s.scalars(q.limit(limite + 1)).all()
    ids = [c.id for c in linhas]
    produtos = dict(s.execute(select(ProdutoDB.comerciante_id, func.count()).where(ProdutoDB.comerciante_id.in_(ids))
                              .group_by(ProdutoDB.comerciante_id)).all()) if ids else {}
    pend = dict(s.execute(select(PendenciaDB.comerciante_id, func.count()).where(
        PendenciaDB.comerciante_id.in_(ids), PendenciaDB.status == "aberta").group_by(PendenciaDB.comerciante_id))
        .all()) if ids else {}
    return _pagina(linhas, limite, lambda c: _comerciante_saida(c, produtos.get(c.id, 0), pend.get(c.id, 0)))


@router.get("/comerciantes/{comerciante_id}", tags=["comerciantes"])
def ver_comerciante(comerciante_id: int, _: AdminToken = Depends(dep.admin_atual), s: Session = Depends(dep.sessao)):
    return _comerciante_saida(_comerciante(s, comerciante_id))


@router.post("/comerciantes", status_code=201, tags=["comerciantes"])
def pre_aprovar(dados: ComercianteNovo, admin: AdminToken = Depends(dep.pode_escrever),
                s: Session = Depends(dep.sessao), acesso=Depends(dep.acesso)):
    telefone = wa_id_brasil(dados.telefone)
    from app.whatsapp.client import variantes_telefone

    c = s.scalar(select(ComercianteDB).where(ComercianteDB.telefone.in_(variantes_telefone(telefone))))
    if c is None:
        c = ComercianteDB(telefone=telefone, nome=dados.nome.strip(), status="ativo", permitido=True)
        s.add(c)
    else:
        c.permitido, c.status = True, "ativo" if c.status == "pendente" else c.status
    s.flush()
    auditar(s, admin.id, admin.email, "pre_aprovar", "comerciante", c.id, {"telefone": mascarar(telefone)})
    acesso.esquecer(c.telefone)
    return _comerciante_saida(c)


@router.patch("/comerciantes/{comerciante_id}", tags=["comerciantes"])
def editar_comerciante(comerciante_id: int, dados: ComercianteEdicao, admin: AdminToken = Depends(dep.pode_escrever),
                       s: Session = Depends(dep.sessao), acesso=Depends(dep.acesso)):
    c = _comerciante(s, comerciante_id)
    antes = {"nome": c.nome, "status": c.status, "permitido": c.permitido}
    for campo, valor in dados.model_dump(exclude_none=True).items():
        setattr(c, campo, valor.strip() if isinstance(valor, str) and campo == "nome" else valor)
    if dados.permitido and dados.status is None and c.status == "pendente":
        c.status = "ativo"  # aprovar = permitir e ativar
    c.atualizado_em = datetime.now(timezone.utc)
    depois = {"nome": c.nome, "status": c.status, "permitido": c.permitido}
    auditar(s, admin.id, admin.email, "editar", "comerciante", c.id,
            {k: [antes[k], depois[k]] for k in antes if antes[k] != depois[k]})
    acesso.esquecer(c.telefone)
    return _comerciante_saida(c)


# --- estoque, livro-caixa, caixa ------------------------------------------------------------------------

@router.get("/comerciantes/{comerciante_id}/produtos", tags=["estoque"])
def produtos(comerciante_id: int, _: AdminToken = Depends(dep.admin_atual), s: Session = Depends(dep.sessao)):
    _comerciante(s, comerciante_id)
    return [{"id": p.id, "produto": p.nome, "unidade": p.unidade, "quantidade": _num(p.quantidade),
             "custo_medio": _num(p.custo_medio), "preco_venda": _num(p.preco_venda), "atualizado_em": p.atualizado_em}
            for p in s.scalars(select(ProdutoDB).where(ProdutoDB.comerciante_id == comerciante_id)
                               .order_by(ProdutoDB.nome))]


@router.post("/comerciantes/{comerciante_id}/produtos/{produto_id}/ajustes", status_code=201, tags=["estoque"])
def ajustar(comerciante_id: int, produto_id: int, dados: Ajuste, admin: AdminToken = Depends(dep.pode_escrever),
            s: Session = Depends(dep.sessao)):
    _comerciante(s, comerciante_id)
    try:
        r = ajustar_produto(s, comerciante_id, produto_id, motivo=dados.motivo, autor=admin.email,
                            quantidade_nova=dados.quantidade_nova, custo_medio_novo=dados.custo_medio_novo)
    except LookupError:
        raise HTTPException(404, "produto não encontrado") from None
    except AjusteInvalido as e:
        raise HTTPException(422, str(e)) from None
    auditar(s, admin.id, admin.email, "ajustar_estoque", "produto", produto_id,
            {"motivo": dados.motivo, "quantidade": _num(r.quantidade), "custo_medio": _num(r.custo_medio),
             "movimentos": r.movimentos})
    return {"produto_id": r.produto_id, "quantidade": _num(r.quantidade), "custo_medio": _num(r.custo_medio),
            "movimentos": r.movimentos}


def _periodo(q, coluna, de: date | None, ate: date | None):
    """Dias no fuso do comerciante (configuração TIMEZONE), com `ate` inclusivo."""
    from zoneinfo import ZoneInfo

    from app.config import get_settings

    tz = ZoneInfo(get_settings().timezone)
    if de:
        q = q.where(coluna >= datetime.combine(de, datetime.min.time(), tz))
    if ate:
        q = q.where(coluna < datetime.combine(ate + timedelta(days=1), datetime.min.time(), tz))
    return q


@router.get("/comerciantes/{comerciante_id}/movimentos", response_model=Pagina, tags=["estoque"])
def movimentos(comerciante_id: int, de: date | None = None, ate: date | None = None,
               tipo: Literal["entrada", "saida", "ajuste", "ajuste_valor"] | None = None,
               limite: int = LIMITE_PADRAO, depois: int | None = None,
               _: AdminToken = Depends(dep.admin_atual), s: Session = Depends(dep.sessao)):
    _comerciante(s, comerciante_id)
    limite = _limite(limite)
    q = _periodo(select(MovimentoDB).where(MovimentoDB.comerciante_id == comerciante_id), MovimentoDB.criado_em, de,
                 ate)
    if tipo:
        q = q.where(MovimentoDB.tipo == tipo)
    if depois:
        q = q.where(MovimentoDB.id < depois)
    linhas = s.scalars(q.order_by(MovimentoDB.id.desc()).limit(limite + 1)).all()
    return _pagina(linhas, limite, lambda m: {
        "id": m.id, "criado_em": m.criado_em, "tipo": m.tipo, "produto": m.produto, "quantidade": _num(m.quantidade),
        "unidade": m.unidade, "valor_unitario": _num(m.valor_unitario), "valor_total": _num(m.valor_total),
        "texto": m.texto, "autor": m.autor or "bot", "motivo": m.motivo, "referencia_id": m.referencia_id,
        "forma_pagamento": m.forma_pagamento, "custo_unitario": _num(m.custo_unitario),
    })


@router.get("/comerciantes/{comerciante_id}/lancamentos", response_model=Pagina, tags=["estoque"])
def lancamentos(comerciante_id: int, de: date | None = None, ate: date | None = None,
                tipo: Literal["despesa", "retirada", "ajuste", "receita"] | None = None,
                limite: int = LIMITE_PADRAO, depois: int | None = None,
                _: AdminToken = Depends(dep.admin_atual), s: Session = Depends(dep.sessao)):
    _comerciante(s, comerciante_id)
    limite = _limite(limite)
    q = _periodo(select(LancamentoDB).where(LancamentoDB.comerciante_id == comerciante_id), LancamentoDB.criado_em,
                 de, ate)
    if tipo:
        q = q.where(LancamentoDB.tipo == tipo)
    if depois:
        q = q.where(LancamentoDB.id < depois)
    linhas = s.scalars(q.order_by(LancamentoDB.id.desc()).limit(limite + 1)).all()
    return _pagina(linhas, limite, lambda l: {
        "id": l.id, "criado_em": l.criado_em, "tipo": l.tipo, "categoria": l.categoria, "descricao": l.descricao,
        "valor": _num(l.valor), "texto": l.texto, "referencia_id": l.referencia_id,
        "forma_pagamento": l.forma_pagamento,
    })


# --- caixa: relatório, fiado e fechamentos (F6) ----------------------------------------------------------

MAX_DIAS_RELATORIO = 366


@router.get("/comerciantes/{comerciante_id}/relatorios/caixa", tags=["caixa"])
def relatorio_caixa(comerciante_id: int, periodo: str = "mes", de: date | None = None, ate: date | None = None,
                    _: AdminToken = Depends(dep.admin_atual), s: Session = Depends(dep.sessao),
                    f=Depends(dep.fabrica), cfg=Depends(dep.configuracoes)):
    """Vendas (por meio), compras, despesas, fiado, entrou/saiu/saldo, lucro bruto, por dia e por produto."""
    from zoneinfo import ZoneInfo

    from app.domain.periodos import Periodo, resolver_periodo
    from app.servicos.relatorios import ServicoRelatorios
    from app.storage.pg_conversa import PgLoja

    _comerciante(s, comerciante_id)
    fuso = cfg.get("fuso_horario", comerciante_id)
    hoje = datetime.now(ZoneInfo(fuso)).date()
    if de or ate:
        de, ate = de or ate, ate or hoje
        if ate < de or (ate - de).days >= MAX_DIAS_RELATORIO:
            raise HTTPException(422, f"período inválido (de <= ate, no máximo {MAX_DIAS_RELATORIO} dias)")
        p = Periodo(de, ate, f"{de:%d/%m/%Y} a {ate:%d/%m/%Y}", "intervalo")
    else:
        p = resolver_periodo(periodo, hoje)
    return ServicoRelatorios(PgLoja(f, comerciante_id, fuso), fuso).calcular(p).como_dict()


def _cliente(c: ClienteDB) -> dict:
    return {"id": c.id, "nome": c.nome, "saldo_devedor": _num(c.saldo_devedor), "divida_desde": c.divida_desde,
            "telefone": c.telefone, "criado_em": c.criado_em, "atualizado_em": c.atualizado_em}


@router.get("/comerciantes/{comerciante_id}/clientes", tags=["caixa"])
def clientes(comerciante_id: int, so_devedores: bool = False, _: AdminToken = Depends(dep.admin_atual),
             s: Session = Depends(dep.sessao)):
    _comerciante(s, comerciante_id)
    q = select(ClienteDB).where(ClienteDB.comerciante_id == comerciante_id)
    if so_devedores:
        q = q.where(ClienteDB.saldo_devedor > 0)
    linhas = s.scalars(q.order_by(ClienteDB.saldo_devedor.desc(), ClienteDB.nome)).all()
    total = sum((c.saldo_devedor for c in linhas if c.saldo_devedor > 0), Decimal(0))
    return {"itens": [_cliente(c) for c in linhas], "total_devido": _num(total)}


@router.get("/comerciantes/{comerciante_id}/clientes/{cliente_id}/extrato", tags=["caixa"])
def extrato_cliente(comerciante_id: int, cliente_id: int, limite: int = 200,
                    _: AdminToken = Depends(dep.admin_atual), s: Session = Depends(dep.sessao)):
    c = s.scalar(select(ClienteDB).where(ClienteDB.id == cliente_id, ClienteDB.comerciante_id == comerciante_id))
    if c is None:
        raise HTTPException(404, "cliente não encontrado")
    linhas = s.scalars(select(FiadoLancamentoDB).where(FiadoLancamentoDB.cliente_id == c.id)
                       .order_by(FiadoLancamentoDB.criado_em.desc(), FiadoLancamentoDB.id.desc())
                       .limit(min(max(limite, 1), 1000))).all()
    return {"cliente": _cliente(c), "lancamentos": [{
        "id": l.id, "criado_em": l.criado_em, "tipo": l.tipo, "valor": _num(l.valor),
        "forma_pagamento": l.forma_pagamento, "movimento_id": l.movimento_id, "referencia_id": l.referencia_id,
        "texto": l.texto} for l in linhas]}


@router.get("/comerciantes/{comerciante_id}/fechamentos", tags=["caixa"])
def fechamentos(comerciante_id: int, limite: int = 60, _: AdminToken = Depends(dep.admin_atual),
                s: Session = Depends(dep.sessao)):
    _comerciante(s, comerciante_id)
    linhas = s.scalars(select(FechamentoDB).where(FechamentoDB.comerciante_id == comerciante_id)
                       .order_by(FechamentoDB.referencia.desc(), FechamentoDB.id.desc())
                       .limit(min(max(limite, 1), 500))).all()
    return {"itens": [{"id": x.id, "tipo": x.tipo, "referencia": x.referencia, "status": x.status, "canal": x.canal,
                       "tentativas": x.tentativas, "erro": x.erro, "criado_em": x.criado_em,
                       "atualizado_em": x.atualizado_em} for x in linhas]}


# --- pendências e mensagens -----------------------------------------------------------------------------

def _pendencia(p: PendenciaDB) -> dict:
    return {"id": p.id, "comerciante_id": p.comerciante_id, "tipo": p.tipo, "resumo": p.resumo,
            "campos_faltando": list(p.campos_faltando or []), "pergunta": (p.rascunho_json or {}).get("pergunta", ""),
            "status": p.status, "criado_em": p.criado_em, "expira_em": p.expira_em}


@router.get("/pendencias", response_model=Pagina, tags=["conversa"])
def pendencias(status: Literal["aberta", "resolvida", "expirada", "cancelada"] | None = "aberta",
               comerciante_id: int | None = None, limite: int = LIMITE_PADRAO, depois: int | None = None,
               _: AdminToken = Depends(dep.admin_atual), s: Session = Depends(dep.sessao)):
    limite = _limite(limite)
    q = select(PendenciaDB)
    if status:
        q = q.where(PendenciaDB.status == status)
    if comerciante_id:
        q = q.where(PendenciaDB.comerciante_id == comerciante_id)
    if depois:
        q = q.where(PendenciaDB.id < depois)
    return _pagina(s.scalars(q.order_by(PendenciaDB.id.desc()).limit(limite + 1)).all(), limite, _pendencia)


@router.post("/pendencias/{pendencia_id}/cancelar", tags=["conversa"])
def cancelar_pendencia(pendencia_id: int, admin: AdminToken = Depends(dep.pode_escrever),
                       s: Session = Depends(dep.sessao)):
    p = s.get(PendenciaDB, pendencia_id)
    if p is None:
        raise HTTPException(404, "pendência não encontrada")
    if p.status != "aberta":
        raise HTTPException(409, f"a pendência já está {p.status}")
    p.status, p.resolvida_em = "cancelada", datetime.now(timezone.utc)
    auditar(s, admin.id, admin.email, "cancelar", "pendencia", p.id)
    return _pendencia(p)


def _mensagem(m: MensagemDB) -> dict:
    return {"id": m.id, "comerciante_id": m.comerciante_id, "telefone_mascarado": m.telefone_mascarado,
            "direcao": m.direcao, "tipo": m.tipo, "texto": m.texto, "transcricao": m.transcricao,
            "intencao": m.intencao_json, "status": m.status, "etapa_erro": m.etapa_erro, "erro": m.erro,
            "tempos": m.tempos_json, "tentativas": m.tentativas, "status_entrega": m.status_entrega,
            "erro_entrega": m.erro_entrega_json, "resposta_a": m.resposta_a, "criado_em": m.criado_em}


@router.get("/mensagens", response_model=Pagina, tags=["conversa"])
def mensagens(status: str | None = None, etapa: str | None = None, comerciante_id: int | None = None,
              direcao: Literal["entrada", "saida"] | None = None, de: date | None = None, ate: date | None = None,
              limite: int = LIMITE_PADRAO, depois: int | None = None,
              _: AdminToken = Depends(dep.admin_atual), s: Session = Depends(dep.sessao)):
    limite = _limite(limite)
    q = _periodo(select(MensagemDB), MensagemDB.criado_em, de, ate)
    if status:
        q = q.where(MensagemDB.status == status)
    if etapa:
        q = q.where(MensagemDB.etapa_erro == etapa)
    if comerciante_id:
        q = q.where(MensagemDB.comerciante_id == comerciante_id)
    if direcao:
        q = q.where(MensagemDB.direcao == direcao)
    if depois:
        q = q.where(MensagemDB.id < depois)
    return _pagina(s.scalars(q.order_by(MensagemDB.id.desc()).limit(limite + 1)).all(), limite, _mensagem)


@router.get("/mensagens/{mensagem_id}", tags=["conversa"])
def ver_mensagem(mensagem_id: int, _: AdminToken = Depends(dep.admin_atual), s: Session = Depends(dep.sessao)):
    m = s.get(MensagemDB, mensagem_id)
    if m is None:
        raise HTTPException(404, "mensagem não encontrada")
    respostas = s.scalars(select(MensagemDB).where(MensagemDB.resposta_a == m.id).order_by(MensagemDB.id)).all()
    return {**_mensagem(m), "respostas": [_mensagem(r) for r in respostas]}


# --- configurações e flags ------------------------------------------------------------------------------

@router.get("/configuracoes", tags=["configuracoes"])
def configuracoes(_: AdminToken = Depends(dep.admin_atual), cfg=Depends(dep.configuracoes)):
    return cfg.listar()


@router.put("/configuracoes/{chave}", tags=["configuracoes"])
def definir_configuracao(chave: str, dados: ValorConfig, comerciante_id: int | None = None,
                         admin: AdminToken = Depends(dep.pode_escrever), cfg=Depends(dep.configuracoes),
                         s: Session = Depends(dep.sessao)):
    if comerciante_id is not None:
        _comerciante(s, comerciante_id)
    try:
        valor = cfg.definir(chave, dados.valor, comerciante_id, admin.email)
    except ConfiguracaoInvalida as e:
        raise HTTPException(422, str(e)) from None
    auditar(s, admin.id, admin.email, "definir", "configuracao", chave, {"valor": valor, "comerciante_id": comerciante_id})
    return {"chave": chave, "valor": valor, "comerciante_id": comerciante_id}


@router.delete("/configuracoes/{chave}", status_code=204, tags=["configuracoes"])
def remover_configuracao(chave: str, comerciante_id: int | None = None, admin: AdminToken = Depends(dep.pode_escrever),
                         cfg=Depends(dep.configuracoes), s: Session = Depends(dep.sessao)):
    cfg.remover(chave, comerciante_id)
    auditar(s, admin.id, admin.email, "remover", "configuracao", chave, {"comerciante_id": comerciante_id})
    return Response(status_code=204)


@router.put("/flags/{chave}", tags=["configuracoes"])
def definir_flag(chave: str, dados: ValorFlag, comerciante_id: int | None = None,
                 admin: AdminToken = Depends(dep.pode_escrever), cfg=Depends(dep.configuracoes),
                 s: Session = Depends(dep.sessao)):
    if comerciante_id is not None:
        _comerciante(s, comerciante_id)
    try:
        cfg.definir_flag(chave, dados.ligado, comerciante_id, admin.email)
    except ConfiguracaoInvalida as e:
        raise HTTPException(422, str(e)) from None
    auditar(s, admin.id, admin.email, "definir", "flag", chave, {"ligado": dados.ligado, "comerciante_id": comerciante_id})
    return {"chave": chave, "ligado": dados.ligado, "comerciante_id": comerciante_id}


@router.delete("/flags/{chave}", status_code=204, tags=["configuracoes"])
def remover_flag(chave: str, comerciante_id: int | None = None, admin: AdminToken = Depends(dep.pode_escrever),
                 cfg=Depends(dep.configuracoes), s: Session = Depends(dep.sessao)):
    cfg.remover_flag(chave, comerciante_id)
    auditar(s, admin.id, admin.email, "remover", "flag", chave, {"comerciante_id": comerciante_id})
    return Response(status_code=204)


# --- exportação -----------------------------------------------------------------------------------------

@router.get("/exportacoes/{nome}.csv", tags=["exportacao"])
def exportar(nome: str, comerciante_id: int | None = None, de: date | None = None, ate: date | None = None,
             status: str | None = None, admin: AdminToken = Depends(dep.admin_atual), f=Depends(dep.fabrica),
             s: Session = Depends(dep.sessao)):
    if nome not in EXPORTACOES:
        raise HTTPException(404, "exportação desconhecida")
    try:
        pedacos = Exportador(f).csv(nome, Filtro(comerciante_id, de, ate, status))
    except ExportacaoInvalida as e:
        raise HTTPException(422, str(e)) from None
    auditar(s, admin.id, admin.email, "exportar", "exportacao", nome, {"comerciante_id": comerciante_id})
    sufixo = f"_{comerciante_id}" if comerciante_id else ""
    return StreamingResponse(pedacos, media_type="text/csv; charset=utf-8",
                             headers={"Content-Disposition": f'attachment; filename="{nome}{sufixo}.csv"'})


@router.get("/comerciantes/{comerciante_id}/exportacao.zip", tags=["exportacao"])
def exportar_pacote(comerciante_id: int, admin: AdminToken = Depends(dep.admin_atual), f=Depends(dep.fabrica),
                    s: Session = Depends(dep.sessao)):
    _comerciante(s, comerciante_id)
    conteudo = Exportador(f).pacote_zip(comerciante_id)
    auditar(s, admin.id, admin.email, "exportar", "exportacao", "pacote", {"comerciante_id": comerciante_id})
    return Response(conteudo, media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="comerciante_{comerciante_id}.zip"'})


# --- painel e saúde -------------------------------------------------------------------------------------

@router.get("/resumo", tags=["painel"])
def resumo(_: AdminToken = Depends(dep.admin_atual), s: Session = Depends(dep.sessao)):
    agora = datetime.now(timezone.utc)
    dia = agora - timedelta(hours=24)
    contar = lambda q: s.scalar(select(func.count()).select_from(q.subquery()))  # noqa: E731
    recentes = s.scalars(select(MensagemDB.tempos_json).where(
        MensagemDB.direcao == "entrada", MensagemDB.criado_em >= dia, MensagemDB.tempos_json.is_not(None))
        .order_by(MensagemDB.id.desc()).limit(500)).all()
    soma: dict[str, list[float]] = {}
    for tempos in recentes:
        for etapa, segundos in (tempos or {}).items():
            soma.setdefault(etapa, []).append(float(segundos))
    return {
        "comerciantes_ativos": contar(select(ComercianteDB.id).where(ComercianteDB.status == "ativo",
                                                                     ComercianteDB.permitido.is_(True))),
        "comerciantes_pendentes": contar(select(ComercianteDB.id).where(ComercianteDB.status == "pendente")),
        "mensagens_24h": contar(select(MensagemDB.id).where(MensagemDB.direcao == "entrada",
                                                            MensagemDB.criado_em >= dia)),
        "erros_24h": contar(select(MensagemDB.id).where(MensagemDB.status == "erro", MensagemDB.criado_em >= dia)),
        "pendencias_abertas": contar(select(PendenciaDB.id).where(PendenciaDB.status == "aberta")),
        "tempo_medio_por_etapa": {e: round(sum(v) / len(v), 3) for e, v in sorted(soma.items())},
    }


@router.get("/saude", tags=["painel"])
def saude(_: AdminToken = Depends(dep.admin_atual), s: Session = Depends(dep.sessao), r=Depends(dep.redis),
          cfg=Depends(dep.configuracoes)):
    try:
        s.execute(text("SELECT 1"))
        banco = "ok"
    except Exception:
        banco = "erro"
    try:
        r.get("saude")
        redis_ok = "ok"
    except Exception:
        redis_ok = "erro"
    return {"banco": banco, "redis": redis_ok, "config_versao": cfg.versao()}


@router.get("/auditoria", response_model=Pagina, tags=["painel"])
def auditoria(limite: int = LIMITE_PADRAO, depois: int | None = None, _: AdminToken = Depends(dep.admin_atual),
              s: Session = Depends(dep.sessao)):
    limite = _limite(limite)
    q = select(AuditoriaDB)
    if depois:
        q = q.where(AuditoriaDB.id < depois)
    return _pagina(s.scalars(q.order_by(AuditoriaDB.id.desc()).limit(limite + 1)).all(), limite, lambda a: {
        "id": a.id, "admin": a.admin_email, "acao": a.acao, "entidade": a.entidade, "entidade_id": a.entidade_id,
        "diff": a.diff_json, "criado_em": a.criado_em})
