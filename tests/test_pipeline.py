"""Pipeline completo com dublês (sem rede, sem banco)."""

from decimal import Decimal

import pytest

from app.domain.models import Comerciante
from app.domain.operacoes import RespostaLLM
from app.errors import ServicoIndisponivel
from app.fila.controle import Controle, MemoriaRedis
from app.llm.interpreter import PROMPT_VERSAO
from app.pipeline import ERRO_GENERICO, Pipeline
from app.speech.tts import AudioGerado
from app.whatsapp.client import MensagemRecebida
from tests.fakes import LojaMemoria


class FakeWhatsApp:
    def __init__(self):
        self.textos, self.audios = [], []

    def baixar_midia(self, media_id):
        return b"audio-" + media_id.encode()

    def enviar_texto(self, para, texto):
        self.textos.append((para, texto))
        return f"wamid.out{len(self.textos)}"

    def enviar_audio(self, para, audio):
        self.audios.append((para, audio))
        return f"wamid.aud{len(self.audios)}"


class FakeSTT:
    def __init__(self, texto="comprei 300 kg de arroz por 300 reais"):
        self.texto = texto

    def transcrever(self, audio, nome_arquivo="audio.ogg"):
        return self.texto


class FakeInterpreter:
    """Devolve as operações de uma fila (uma resposta por mensagem) e guarda o contexto recebido."""

    def __init__(self, *respostas, erro=None):
        self.respostas = list(respostas)
        self.erro = erro
        self.contextos = []

    def interpretar(self, texto, contexto):
        self.contextos.append(contexto)
        if self.erro:
            raise self.erro
        ops = self.respostas.pop(0) if len(self.respostas) > 1 else self.respostas[0]
        return RespostaLLM.model_validate({"operacoes": ops})


class FakeTTS:
    def sintetizar(self, texto):
        return AudioGerado(b"ogg:" + texto.encode(), "audio/ogg", "ogg")


class FakeTenants:
    def __init__(self):
        self.comerciantes = {}

    def obter_ou_criar(self, telefone, nome, agora):
        if telefone in self.comerciantes:
            return self.comerciantes[telefone], False
        c = Comerciante(telefone, nome or "", "ativo", agora, id=len(self.comerciantes) + 1)
        self.comerciantes[telefone] = c
        return c, True


class FakeMensagens:
    def __init__(self):
        self.linhas = {}  # id -> dict
        self.por_wa = {}
        self.saidas = []

    def registrar_entrada(self, comerciante_id, wa_msg_id, tipo, texto="", telefone_mascarado=""):
        if wa_msg_id in self.por_wa:
            linha = self.linhas[self.por_wa[wa_msg_id]]
            linha["tentativas"] += 1
            return linha["id"], linha["status"]
        mid = len(self.linhas) + 1
        self.linhas[mid] = dict(id=mid, comerciante_id=comerciante_id, wa=wa_msg_id, tipo=tipo, texto=texto,
                                status="recebida", tentativas=1, telefone_mascarado=telefone_mascarado)
        self.por_wa[wa_msg_id] = mid
        return mid, "recebida"

    def atualizar(self, mensagem_id, **campos):
        self.linhas[mensagem_id].update(campos)

    def registrar_saida(self, comerciante_id, resposta_a, tipo, texto, wa_msg_id, erro=""):
        self.saidas.append(dict(resposta_a=resposta_a, tipo=tipo, texto=texto, wa=wa_msg_id, erro=erro))

    def historico(self, comerciante_id, desde, limite=6):
        return []

    def de(self, wa):
        return self.linhas[self.por_wa[wa]]


COMPRA = [{"tipo": "compra", "itens": [{"produto": "arroz", "quantidade": 300, "unidade": "kg",
                                         "valor_total": 300}]}]


@pytest.fixture
def ambiente():
    lojas = {}
    wa, tenants, mensagens = FakeWhatsApp(), FakeTenants(), FakeMensagens()

    def criar(interpreter=None, stt=None, **kw):
        return Pipeline(
            whatsapp=wa, stt=stt or FakeSTT(), interpreter=interpreter or FakeInterpreter(COMPRA), tts=FakeTTS(),
            tenants=tenants, loja=lambda c: lojas.setdefault(c.telefone, LojaMemoria()), mensagens=mensagens,
            controle=Controle(MemoriaRedis()), **kw,
        )

    return criar, wa, lojas, mensagens


def audio(msg_id="m1", telefone="5585999990001"):
    return MensagemRecebida(telefone=telefone, msg_id=msg_id, tipo="audio", nome="Zé", media_id="x")


def test_primeira_mensagem_cadastra_grava_e_responde_com_boas_vindas(ambiente):
    criar, wa, lojas, mensagens = ambiente
    criar().processar(audio())

    loja = lojas["5585999990001"]
    assert loja.estoque["arroz"].quantidade == 300
    (para, texto), = wa.textos
    assert para == "5585999990001"
    assert texto.startswith("Olá, Zé! Seu cadastro foi feito.")
    assert "agora tem 300 quilos" in texto
    assert wa.audios == [(para, AudioGerado(b"ogg:" + texto.encode(), "audio/ogg", "ogg"))]


def test_mensagem_registrada_com_status_intencao_tempos_e_saidas(ambiente):
    criar, wa, _, mensagens = ambiente
    criar().processar(audio())
    linha = mensagens.de("m1")
    assert linha["status"] == "processada" and linha["transcricao"].startswith("comprei 300 kg")
    assert linha["intencao_json"]["prompt"] == PROMPT_VERSAO
    assert linha["intencao_json"]["operacoes"][0]["tipo"] == "compra"
    assert {"cadastro", "download_midia", "transcricao", "contexto", "interpretacao", "dominio", "banco", "tts",
            "envio_audio", "envio_texto"} <= set(linha["tempos_json"])
    assert [(s["tipo"], s["wa"]) for s in mensagens.saidas] == [("audio", "wamid.aud1"), ("text", "wamid.out1")]
    assert linha["telefone_mascarado"] == "5585****0001"


def test_mesma_mensagem_duas_vezes_nao_duplica(ambiente):
    criar, wa, lojas, _ = ambiente
    p = criar()
    p.processar(audio("m1"))
    p.processar(audio("m1"))
    assert len(lojas["5585999990001"].movimentos) == 1
    assert len(wa.textos) == 1


def test_redis_perdeu_a_marca_mas_o_banco_lembra(ambiente):
    criar, wa, lojas, _ = ambiente
    criar().processar(audio("m1"))
    criar().processar(audio("m1"))  # outro Controle (Redis vazio)
    assert len(lojas["5585999990001"].movimentos) == 1 and len(wa.textos) == 1


def test_comerciantes_separados(ambiente):
    criar, _, lojas, _ = ambiente
    p = criar()
    p.processar(audio("m1", "5585000000001"))
    p.processar(audio("m2", "5585000000002"))
    assert set(lojas) == {"5585000000001", "5585000000002"}


def test_mensagem_de_texto_nao_usa_stt(ambiente):
    criar, _, lojas, mensagens = ambiente
    criar().processar(MensagemRecebida("5585", "t1", "text", texto="comprei 300 kg de arroz"))
    assert lojas["5585"].movimentos[0].texto_transcrito == "comprei 300 kg de arroz"
    assert "transcricao" not in mensagens.de("t1")["tempos_json"]


def test_erro_no_llm_registra_a_etapa_e_responde_amigavel(ambiente):
    criar, wa, _, mensagens = ambiente
    criar(FakeInterpreter(erro=ValueError("bug"))).processar(audio())
    assert wa.textos[-1][1] == ERRO_GENERICO
    linha = mensagens.de("m1")
    assert (linha["status"], linha["etapa_erro"]) == ("erro", "interpretacao")
    assert linha["erro"] == "ValueError: bug"


def test_servico_indisponivel_propaga_e_marca_reprocessando(ambiente):
    criar, wa, lojas, mensagens = ambiente
    with pytest.raises(ServicoIndisponivel):
        criar(FakeInterpreter(erro=ServicoIndisponivel("429"))).processar(audio())
    assert wa.textos == []
    assert (mensagens.de("m1")["status"], mensagens.de("m1")["etapa_erro"]) == ("reprocessando", "interpretacao")
    criar().processar(audio())  # nova tentativa da fila
    assert len(lojas["5585999990001"].movimentos) == 1
    assert mensagens.de("m1")["tentativas"] == 2 and mensagens.de("m1")["status"] == "processada"


def test_falha_no_audio_ainda_manda_texto_e_registra(ambiente):
    criar, wa, _, mensagens = ambiente

    class TTSQuebrado:
        def sintetizar(self, texto):
            raise RuntimeError("TTS fora do ar")

    p = criar()
    p._tts = TTSQuebrado()
    p.processar(audio())
    assert wa.audios == [] and len(wa.textos) == 1
    assert mensagens.de("m1")["status"] == "processada"
    assert [s["erro"] for s in mensagens.saidas] == ["RuntimeError: TTS fora do ar", ""]


def test_resposta_so_em_texto_por_comerciante(ambiente):
    criar, wa, _, _ = ambiente
    criar(resposta_em_audio=lambda c: False).processar(audio())
    assert wa.audios == [] and len(wa.textos) == 1


def test_bloqueado_e_ignorado(ambiente):
    criar, wa, _, mensagens = ambiente
    p = criar()
    p.processar(audio("m1"))
    p._tenants.comerciantes["5585999990001"].status = "bloqueado"
    p.processar(audio("m2"))
    assert len(wa.textos) == 1 and mensagens.de("m2")["status"] == "ignorada"


def test_dialogo_do_feijao_ponta_a_ponta(ambiente):
    """As 3 falas reais, com o interpretador devolvendo o que o prompt v2 devolveu no Groq."""
    criar, wa, lojas, _ = ambiente
    interp = FakeInterpreter(
        [{"tipo": "compra", "itens": [{"produto": "feijão", "quantidade": 10, "unidade": "kg"}]}],
        [{"tipo": "venda", "itens": [{"produto": "feijão", "quantidade": 2}]}],
        [{"tipo": "completar", "pendencia_id": 1, "campos": {"valor_unitario": 10}, "confianca": 0.95}],
    )
    p = criar(interpreter=interp, stt=FakeSTT("fala"))
    p.processar(audio("a1"))
    assert wa.textos[-1][1].endswith("Quanto você pagou no feijão? Pode falar o preço do quilo, da unidade ou o "
                                     "total.")
    p.processar(audio("a2"))
    p.processar(audio("a3"))
    assert wa.textos[-1][1].startswith("Certo! O feijão saiu a 10 reais o quilo, 100 reais no total.")
    feijao = lojas["5585999990001"].estoque["feijao"]
    assert (feijao.quantidade, feijao.custo_medio) == (8, Decimal("10.0000"))
    # o contexto da 3ª fala trazia a pendência para o LLM
    assert interp.contextos[2].pendencias[0]["faltando"] == ["valor"]


# --- F6: relatório, anexos, botão do fechamento e preferências --------------------------------------------

class WhatsAppComAnexos(FakeWhatsApp):
    def __init__(self):
        super().__init__()
        self.anexos = []

    def enviar_imagem(self, para, conteudo, mime, legenda=""):
        self.anexos.append(("image", legenda))
        return "wamid.img"

    def enviar_documento(self, para, conteudo, nome, mime, legenda=""):
        self.anexos.append(("document", nome))
        return "wamid.doc"


def test_relatorio_vai_em_texto_e_a_voz_fala_so_o_resumo():
    from app.servicos.relatorios import ServicoRelatorios

    wa, lojas, mensagens = WhatsAppComAnexos(), {}, FakeMensagens()
    interp = FakeInterpreter(COMPRA, [{"tipo": "consulta", "assunto": "caixa", "periodo": "hoje"}],
                             [{"tipo": "consulta", "assunto": "caixa", "periodo": "hoje", "formato": "planilha"}])
    p = Pipeline(whatsapp=wa, stt=FakeSTT(), interpreter=interp, tts=FakeTTS(), tenants=FakeTenants(),
                 loja=lambda c: lojas.setdefault(c.telefone, LojaMemoria()), mensagens=mensagens,
                 controle=Controle(MemoriaRedis()),
                 relatorios=lambda c: ServicoRelatorios(lojas[c.telefone]))
    p.processar(audio("m1"))
    p.processar(audio("m2"))
    _, texto = wa.textos[-1]
    _, voz = wa.audios[-1]
    assert texto.startswith("Caixa: hoje") and "```" in texto and "R$ 300,00" in texto
    assert b"```" not in voz.conteudo and voz.conteudo.decode().endswith("Mandei a tabela por escrito.")
    assert mensagens.de("m2")["intencao_json"]["relatorios"] == [{"periodo": "hoje", "formato": "resumo"}]

    p.processar(audio("m3"))
    assert wa.anexos == [("document", next(n for t, n in wa.anexos))]
    assert wa.anexos[0][1].startswith("caixa_") and wa.anexos[0][1].endswith(".csv")
    assert mensagens.saidas[-1]["tipo"] == "document"


def test_botao_do_fechamento_nao_chama_a_ia():
    from app.servicos.relatorios import ServicoRelatorios

    wa, lojas, mensagens = WhatsAppComAnexos(), {}, FakeMensagens()
    interp = FakeInterpreter(COMPRA)
    p = Pipeline(whatsapp=wa, stt=FakeSTT(), interpreter=interp, tts=FakeTTS(), tenants=FakeTenants(),
                 loja=lambda c: lojas.setdefault(c.telefone, LojaMemoria()), mensagens=mensagens,
                 controle=Controle(MemoriaRedis()), relatorios=lambda c: ServicoRelatorios(lojas[c.telefone]),
                 resposta_em_audio=lambda c: False)
    p.processar(audio("m1"))
    chamadas = len(interp.contextos)
    p.processar(MensagemRecebida(telefone="5585999990001", msg_id="b1", tipo="button", texto="Ver detalhes",
                                 payload="caixa|hoje|itens"))
    assert len(interp.contextos) == chamadas
    assert wa.textos[-1][1].startswith("Vendido: hoje")


def test_preferencias_e_aviso():
    gravadas, wa = [], FakeWhatsApp()
    interp = FakeInterpreter([{"tipo": "configurar", "campos": {"fechamento_horario": "20h"}}])
    p = Pipeline(whatsapp=wa, stt=FakeSTT(), interpreter=interp, tts=FakeTTS(), tenants=FakeTenants(),
                 loja=lambda c: LojaMemoria(), mensagens=FakeMensagens(), controle=Controle(MemoriaRedis()),
                 configurar=lambda c, v: gravadas.append(v), aviso=lambda c: "Aviso do fechamento.",
                 resposta_em_audio=lambda c: False)
    p.processar(audio("m1"))
    assert gravadas == [{"fechamento_diario_horario": "20:00"}]
    assert wa.textos[-1][1].endswith("Pode chegar até 15 minutos depois.\n\nAviso do fechamento.")
