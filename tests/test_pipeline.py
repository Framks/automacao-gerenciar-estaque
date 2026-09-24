"""Pipeline completo com dublês (sem rede)."""

import pytest

from app.domain.models import Acao, Intencao, Item
from app.domain.units import chave_produto
from app.errors import ServicoIndisponivel
from app.fila.controle import Controle, MemoriaRedis
from app.pipeline import ERRO_GENERICO, Pipeline
from app.speech.tts import AudioGerado
from app.storage.tenants import Comerciante
from app.whatsapp.client import MensagemRecebida


class FakeWhatsApp:
    def __init__(self):
        self.textos, self.audios = [], []

    def baixar_midia(self, media_id):
        return b"audio-" + media_id.encode()

    def enviar_texto(self, para, texto):
        self.textos.append((para, texto))

    def enviar_audio(self, para, audio):
        self.audios.append((para, audio))


class FakeSTT:
    def __init__(self):
        self.recebido = None

    def transcrever(self, audio, nome_arquivo="audio.ogg"):
        self.recebido = audio
        return "comprei 300 kg de arroz por 300 reais"


class FakeInterpreter:
    def __init__(self, intencao=None, erro=None):
        self.intencao, self.erro = intencao, erro

    def interpretar(self, texto, produtos):
        if self.erro:
            raise self.erro
        return self.intencao


class FakeTTS:
    def sintetizar(self, texto):
        return AudioGerado(b"ogg:" + texto.encode(), "audio/ogg", "ogg")


class FakeTenants:
    def __init__(self):
        self.comerciantes = {}

    def obter_ou_criar(self, telefone, nome, agora):
        if telefone in self.comerciantes:
            return self.comerciantes[telefone], False
        c = Comerciante(telefone, nome or "", "ativo", agora)
        self.comerciantes[telefone] = c
        return c, True


class FakeRepo:
    def __init__(self):
        self.estoque, self.movimentos = {}, []

    def carregar_estoque(self):
        return dict(self.estoque)

    def carregar_movimentos(self):
        return list(self.movimentos)

    def salvar(self, movimentos, estoque_alterado):
        self.movimentos += movimentos
        for p in estoque_alterado:
            p.linha = p.linha or len(self.estoque) + 2
            self.estoque[chave_produto(p.produto)] = p


COMPRA = Intencao(acao=Acao.COMPRA, itens=[Item(produto="arroz", quantidade=300, unidade="kg", valor_total=300)])


@pytest.fixture
def ambiente():
    repos = {}
    wa, tenants = FakeWhatsApp(), FakeTenants()
    interp = FakeInterpreter(COMPRA)

    def criar(interpreter=interp):
        return Pipeline(
            whatsapp=wa, stt=FakeSTT(), interpreter=interpreter, tts=FakeTTS(), tenants=tenants,
            repositorio=lambda sid: repos.setdefault(sid, FakeRepo()),
            controle=Controle(MemoriaRedis()),
        )

    return criar, wa, repos, interp


def audio(msg_id="m1", telefone="5585999990001"):
    return MensagemRecebida(telefone=telefone, msg_id=msg_id, tipo="audio", nome="Zé", media_id="x")


def test_primeira_mensagem_cadastra_grava_e_responde_com_boas_vindas(ambiente):
    criar, wa, repos, _ = ambiente
    criar().processar(audio())

    repo = repos["5585999990001"]
    assert repo.estoque["arroz"].quantidade == 300
    assert len(repo.movimentos) == 1
    (para, texto), = wa.textos
    assert para == "5585999990001"
    assert texto.startswith("Olá, Zé! Seu cadastro foi feito.")
    assert "agora tem 300 quilos" in texto
    assert wa.audios == [(para, AudioGerado(b"ogg:" + texto.encode(), "audio/ogg", "ogg"))]


def test_segunda_mensagem_nao_tem_boas_vindas_e_soma(ambiente):
    criar, wa, repos, _ = ambiente
    p = criar()
    p.processar(audio("m1"))
    p.processar(audio("m2"))
    assert repos["5585999990001"].estoque["arroz"].quantidade == 600
    assert wa.textos[1][1].startswith("Anotado!")


def test_mesma_mensagem_duas_vezes_nao_duplica(ambiente):
    criar, wa, repos, _ = ambiente
    p = criar()
    p.processar(audio("m1"))
    p.processar(audio("m1"))
    assert len(repos["5585999990001"].movimentos) == 1
    assert len(wa.textos) == 1


def test_comerciantes_separados(ambiente):
    criar, _, repos, _ = ambiente
    p = criar()
    p.processar(audio("m1", "5585000000001"))
    p.processar(audio("m2", "5585000000002"))
    assert set(repos) == {"5585000000001", "5585000000002"}
    assert all(len(r.movimentos) == 1 for r in repos.values())


def test_mensagem_de_texto_nao_usa_stt(ambiente):
    criar, wa, repos, _ = ambiente
    criar().processar(MensagemRecebida("5585", "t1", "text", texto="comprei 300 kg de arroz"))
    assert repos["5585"].movimentos[0].texto_transcrito == "comprei 300 kg de arroz"


def test_erro_inesperado_responde_mensagem_amigavel(ambiente):
    criar, wa, repos, _ = ambiente
    criar(FakeInterpreter(erro=ValueError("bug"))).processar(audio())
    assert wa.textos[-1][1] == ERRO_GENERICO


def test_servico_indisponivel_propaga_para_a_fila_tentar_de_novo(ambiente):
    criar, wa, repos, interp = ambiente
    with pytest.raises(ServicoIndisponivel):
        criar(FakeInterpreter(erro=ServicoIndisponivel("429"))).processar(audio())
    assert wa.textos == []
    # na nova tentativa, processa normalmente
    criar(interp).processar(audio())
    assert len(repos["5585999990001"].movimentos) == 1


def test_falha_no_audio_ainda_manda_texto(ambiente):
    criar, wa, _, _ = ambiente
    class TTSQuebrado:
        def sintetizar(self, texto):
            raise RuntimeError("TTS fora do ar")

    p = criar()
    p._tts = TTSQuebrado()
    p.processar(audio())
    assert wa.audios == [] and len(wa.textos) == 1
