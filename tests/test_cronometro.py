import pytest

from app.observabilidade.cronometro import Cronometro


def relogio(*marcas):
    it = iter(marcas)
    return lambda: next(it)


def test_tempos_por_etapa_e_total():
    c = Cronometro(relogio(0, 1.5, 2, 2.25))
    with c.etapa("transcricao"):
        pass
    with c.etapa("interpretacao"):
        pass
    assert c.tempos == {"transcricao": 1.5, "interpretacao": 0.25}
    assert c.total == 1.75 and c.etapa_erro == ""


def test_erro_marca_a_etapa_e_propaga():
    c = Cronometro(relogio(0, 1, 1, 3))
    with c.etapa("transcricao"):
        pass
    with pytest.raises(ValueError):
        with c.etapa("interpretacao"):
            raise ValueError("groq")
    assert c.etapa_erro == "interpretacao" and c.tempos["interpretacao"] == 2
