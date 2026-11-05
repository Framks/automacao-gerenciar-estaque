"""Allowlist no banco: número novo fica pendente; aprovar libera; nono dígito não importa."""

from sqlalchemy import select, update

from app.db.engine import transacao
from app.db.modelos import ComercianteDB
from app.fila.controle import MemoriaRedis
from app.servicos.comerciantes import Acesso, importar_permitidos
from app.whatsapp.client import variantes_telefone, wa_id_brasil


def test_wa_id_e_variantes():
    assert wa_id_brasil("5588981061891") == "558881061891"
    assert wa_id_brasil("+55 88 98106-1891") == "558881061891"
    assert wa_id_brasil("14155550000") == "14155550000"
    assert variantes_telefone("558881061891") == {"558881061891", "5588981061891"}


def test_numero_novo_fica_pendente_e_aprovar_libera(fabrica_pg):
    redis = MemoriaRedis()
    acesso = Acesso(fabrica_pg, redis)
    assert acesso.verificar("558899990000", "Desconhecido") == "pendente"
    with transacao(fabrica_pg) as s:
        linha = s.scalar(select(ComercianteDB))
        assert (linha.telefone, linha.status, linha.permitido, linha.nome) == (
            "558899990000", "pendente", False, "Desconhecido")
        s.execute(update(ComercianteDB).values(status="ativo", permitido=True))
    assert acesso.verificar("558899990000") == "pendente"  # cache: ainda não sabe
    acesso.esquecer("558899990000")
    assert acesso.verificar("558899990000") == "permitido"


def test_bloqueado(fabrica_pg):
    with transacao(fabrica_pg) as s:
        s.add(ComercianteDB(telefone="558811112222", status="bloqueado", permitido=True))
    assert Acesso(fabrica_pg, MemoriaRedis()).verificar("558811112222") == "bloqueado"


def test_importar_lista_com_e_sem_nono_digito(fabrica_pg):
    with transacao(fabrica_pg) as s:
        s.add(ComercianteDB(telefone="558881061891", nome="Francisco", status="ativo", permitido=False))
        s.add(ComercianteDB(telefone="558592366067", status="pendente"))
    gravados = importar_permitidos(fabrica_pg, "5588981061891, 558592366067,+5588992929364")
    assert gravados == ["558881061891", "558592366067", "558892929364"]
    acesso = Acesso(fabrica_pg, MemoriaRedis())
    assert {acesso.verificar(t) for t in gravados} == {"permitido"}
    assert importar_permitidos(fabrica_pg, "5588981061891") == ["558881061891"]  # de novo: não duplica
    with transacao(fabrica_pg) as s:
        assert len(s.scalars(select(ComercianteDB)).all()) == 3
