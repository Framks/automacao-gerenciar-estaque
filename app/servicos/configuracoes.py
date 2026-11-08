"""Configurações e feature flags no banco, com cache curto. Variáveis de ambiente ficam só para segredos.

- `get(chave, comerciante_id)`: valor do comerciante -> global -> padrão em código.
- Registro central (DEFINICOES, FLAGS): chave desconhecida ou valor inválido são recusados na escrita.
- Cache em processo por até 60 s; uma versão no Redis (`config:versao`) invalida antes, a cada escrita.
- Se o banco falhar na leitura, valem os padrões em código (com log), para o bot não cair por configuração.
"""

import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, sessionmaker

from app.db.engine import transacao
from app.db.modelos import ConfiguracaoDB, FeatureFlagDB
from app.domain.financeiro import CATEGORIAS
from app.domain.pagamento import FORMAS, NAO_INFORMADO

log = logging.getLogger(__name__)

VERSAO = "config:versao"
TTL = 60
VOZES = ["pt-BR-FranciscaNeural", "pt-BR-AntonioNeural", "pt-BR-ThalitaMultilingualNeural"]


class ConfiguracaoInvalida(ValueError):
    pass


@dataclass(frozen=True)
class Definicao:
    tipo: str  # int | float | decimal | bool | str | enum | list
    padrao: Any
    descricao: str
    minimo: float | None = None
    maximo: float | None = None
    opcoes: tuple[str, ...] = ()
    por_comerciante: bool = True

    def validar(self, valor: Any) -> Any:
        """Devolve o valor normalizado (pronto para JSON) ou lança ConfiguracaoInvalida."""
        try:
            if self.tipo == "int":
                if isinstance(valor, bool) or float(valor) != int(float(valor)):
                    raise ValueError
                v: Any = int(float(valor))
            elif self.tipo in ("float", "decimal"):
                if isinstance(valor, bool):
                    raise ValueError
                v = float(Decimal(str(valor)))
            elif self.tipo == "bool":
                if not isinstance(valor, bool):
                    raise ValueError
                v = valor
            elif self.tipo in ("str", "enum"):
                v = str(valor).strip()
                if not v:
                    raise ValueError
            elif self.tipo == "list":
                if not isinstance(valor, list) or not all(isinstance(x, str) and x.strip() for x in valor):
                    raise ValueError
                v = [x.strip() for x in valor]
                if self.opcoes and any(x not in self.opcoes for x in v):
                    raise ConfiguracaoInvalida(f"opção inválida em {v} (opções: {', '.join(self.opcoes)})")
            elif self.tipo == "texto":  # pode ser vazio
                v = "" if valor is None else str(valor).strip()
            elif self.tipo == "horario":  # "" (desligado) ou HH:MM em intervalos de 15 minutos
                v = "" if valor is None else str(valor).strip()
                if v:
                    h, m = v.split(":")
                    if not (0 <= int(h) <= 23 and int(m) in (0, 15, 30, 45) and len(m) == 2):
                        raise ValueError
                    v = f"{int(h):02d}:{m}"
            elif self.tipo == "fuso":
                from zoneinfo import ZoneInfo

                v = str(valor).strip()
                ZoneInfo(v)
            else:
                raise ValueError
        except ConfiguracaoInvalida:
            raise
        except (TypeError, ValueError, ArithmeticError, LookupError):
            raise ConfiguracaoInvalida(f"valor inválido para {self.tipo}: {valor!r}") from None
        if self.minimo is not None and v < self.minimo or self.maximo is not None and v > self.maximo:
            raise ConfiguracaoInvalida(f"fora do intervalo [{self.minimo}, {self.maximo}]: {v}")
        if self.tipo == "enum" and v not in self.opcoes:
            raise ConfiguracaoInvalida(f"opção inválida: {v} (opções: {', '.join(self.opcoes)})")
        return v


DEFINICOES: dict[str, Definicao] = {
    "groq_model": Definicao("str", "openai/gpt-oss-120b", "Modelo do Groq que interpreta as falas",
                            por_comerciante=False),
    "groq_stt_model": Definicao("str", "whisper-large-v3-turbo", "Modelo do Groq que transcreve os áudios",
                                por_comerciante=False),
    "tts_voz": Definicao("enum", "pt-BR-FranciscaNeural", "Voz das respostas em áudio", opcoes=tuple(VOZES)),
    "tts_velocidade": Definicao("str", "-5%", "Velocidade da voz (ex.: -10%, +0%, +10%)"),
    "contexto_max_mensagens": Definicao("int", 6, "Mensagens recentes enviadas como contexto à IA", 0, 20),
    "contexto_janela_horas": Definicao("int", 2, "Até quantas horas atrás a conversa entra no contexto", 0, 48),
    "contexto_max_produtos": Definicao("int", 40, "Produtos do catálogo enviados à IA", 1, 200),
    "confirmacao_confianca_minima": Definicao("float", 0.7, "Abaixo desta confiança, o bot confirma antes", 0, 1),
    "confirmacao_valor_limite": Definicao("decimal", 1000, "Acima deste valor (R$), o bot confirma antes", 0),
    "pendencia_validade_horas": Definicao("int", 24, "Por quanto tempo uma pergunta fica esperando resposta", 1, 720),
    "retencao_mensagens_dias": Definicao("int", 90, "Dias que as mensagens ficam guardadas", 7, 3650,
                                         por_comerciante=False),
    "categorias_despesa": Definicao("list", list(CATEGORIAS), "Categorias de despesa aceitas"),
    "contexto_max_clientes": Definicao("int", 20, "Nomes de clientes do fiado enviados à IA", 0, 100),
    "formas_pagamento": Definicao("list", list(FORMAS), "Meios de pagamento aceitos (o bot pergunta entre eles)",
                                  opcoes=FORMAS),
    "pagamento_padrao": Definicao("enum", NAO_INFORMADO, "Meio anotado quando a venda não diz como foi paga",
                                  opcoes=(NAO_INFORMADO, "dinheiro", "pix", "cartao")),
    "fuso_horario": Definicao("fuso", "America/Fortaleza", "Fuso do comerciante (dias do relatório e fechamento)"),
    "fechamento_diario_horario": Definicao("horario", "", "Horário do fechamento do dia (vazio = desligado)"),
    "fechamento_mensal": Definicao("bool", False, "Mandar o fechamento do mês anterior no dia 1"),
    "fechamento_formato": Definicao("enum", "resumo", "Formato do fechamento do dia", opcoes=("resumo", "itens")),
    "fechamento_dia_vazio": Definicao("bool", True, "Mandar o fechamento mesmo sem movimento no dia"),
    "fechamento_mensal_grafico": Definicao("bool", True, "Mandar o gráfico junto do fechamento do mês"),
    "fechamento_template": Definicao("texto", "", "Template aprovado na Meta para o fechamento fora da janela "
                                                  "de 24h (vazio = só manda dentro da janela)", por_comerciante=False),
    "fechamento_template_idioma": Definicao("str", "pt_BR", "Idioma do template do fechamento",
                                            por_comerciante=False),
}

# o que o próprio comerciante pode mudar pela conversa (operação "configurar"); o resto é só pelo admin
CONFIG_DO_COMERCIANTE = ("fechamento_diario_horario", "fechamento_mensal", "fechamento_formato", "pagamento_padrao")


@dataclass(frozen=True)
class DefinicaoFlag:
    padrao: bool
    descricao: str


FLAGS: dict[str, DefinicaoFlag] = {
    "pendencias_ativas": DefinicaoFlag(True, "Perguntar o que faltou (valor, quantidade, categoria)"),
    "perguntar_valor_compra": DefinicaoFlag(True, "Perguntar o valor de uma compra anotada sem valor"),
    "confirmacao_ambiguidade": DefinicaoFlag(True, "Confirmar antes de gravar quando houver dúvida"),
    "despesas": DefinicaoFlag(True, "Anotar despesas e retiradas do caixa"),
    "resposta_em_audio": DefinicaoFlag(True, "Responder também em áudio (desligado: só texto)"),
    "fiado": DefinicaoFlag(True, "Anotar vendas fiado, pagamentos e dívidas de clientes"),
    "perguntar_pagamento": DefinicaoFlag(False, "Perguntar o meio de pagamento quando a venda não disser"),
    "fechamento_automatico": DefinicaoFlag(True, "Mandar o fechamento do caixa no horário escolhido"),
}


@dataclass
class _Cache:
    config: dict[tuple[str, int | None], Any] = field(default_factory=dict)
    flags: dict[tuple[str, int | None], bool] = field(default_factory=dict)
    carregado_em: float = -1e9
    versao: str | None = None
    versao_vista_em: float = -1e9


class Configuracoes:
    def __init__(self, fabrica: sessionmaker[Session] | None, redis, ttl: int = TTL,
                 relogio: Callable[[], float] = time.monotonic, intervalo_versao: float = 5.0):
        self._fabrica = fabrica
        self._redis = redis
        self._ttl = ttl
        self._relogio = relogio
        self._intervalo_versao = intervalo_versao
        self._cache = _Cache()

    # --- leitura ---------------------------------------------------------------------------------------

    def get(self, chave: str, comerciante_id: int | None = None) -> Any:
        definicao = DEFINICOES.get(chave)
        if definicao is None:
            raise KeyError(f"configuração desconhecida: {chave}")
        self._recarregar_se_preciso()
        if comerciante_id is not None and definicao.por_comerciante and (chave, comerciante_id) in self._cache.config:
            return self._cache.config[(chave, comerciante_id)]
        return self._cache.config.get((chave, None), definicao.padrao)

    def ligada(self, chave: str, comerciante_id: int | None = None) -> bool:
        definicao = FLAGS.get(chave)
        if definicao is None:
            raise KeyError(f"flag desconhecida: {chave}")
        self._recarregar_se_preciso()
        if comerciante_id is not None and (chave, comerciante_id) in self._cache.flags:
            return self._cache.flags[(chave, comerciante_id)]
        return self._cache.flags.get((chave, None), definicao.padrao)

    def versao(self) -> str:
        try:
            return str(self._redis.get(VERSAO) or "0")
        except Exception:
            return "?"

    def _recarregar_se_preciso(self) -> None:
        agora = self._relogio()
        if agora - self._cache.versao_vista_em >= self._intervalo_versao:
            versao = self.versao()
            self._cache.versao_vista_em = agora
            if versao != self._cache.versao:
                self._cache.carregado_em = -1e9  # alguém escreveu: recarrega já
                self._cache.versao = versao
        if agora - self._cache.carregado_em < self._ttl:
            return
        self._cache.carregado_em = agora
        if self._fabrica is None:
            return
        try:
            with transacao(self._fabrica) as s:
                config = {(c.chave, c.comerciante_id): c.valor_json for c in s.scalars(select(ConfiguracaoDB))
                          if c.chave in DEFINICOES}
                flags = {(f.chave, f.comerciante_id): f.ligado for f in s.scalars(select(FeatureFlagDB))
                         if f.chave in FLAGS}
            self._cache.config, self._cache.flags = config, flags
        except Exception:
            log.exception("não consegui ler as configurações; mantendo as anteriores (ou os padrões)")

    # --- escrita (admin) -------------------------------------------------------------------------------

    def definir(self, chave: str, valor: Any, comerciante_id: int | None = None, autor: str = "") -> Any:
        definicao = DEFINICOES.get(chave)
        if definicao is None:
            raise ConfiguracaoInvalida(f"configuração desconhecida: {chave}")
        if comerciante_id is not None and not definicao.por_comerciante:
            raise ConfiguracaoInvalida(f"{chave} só existe como configuração global")
        normalizado = definicao.validar(valor)
        with transacao(self._fabrica) as s:
            s.execute(insert(ConfiguracaoDB).values(
                chave=chave, comerciante_id=comerciante_id, valor_json=normalizado, atualizado_por=autor,
            ).on_conflict_do_update(constraint="uq_configuracoes_chave",
                                    set_={"valor_json": normalizado, "atualizado_por": autor,
                                          "atualizado_em": _agora_sql()}))
        self._mudou()
        return normalizado

    def remover(self, chave: str, comerciante_id: int | None = None) -> None:
        """Volta para o valor global (se for de comerciante) ou para o padrão em código."""
        with transacao(self._fabrica) as s:
            s.execute(delete(ConfiguracaoDB).where(ConfiguracaoDB.chave == chave, _comerciante(ConfiguracaoDB,
                                                                                               comerciante_id)))
        self._mudou()

    def definir_flag(self, chave: str, ligado: bool, comerciante_id: int | None = None, autor: str = "") -> None:
        if chave not in FLAGS:
            raise ConfiguracaoInvalida(f"flag desconhecida: {chave}")
        if not isinstance(ligado, bool):
            raise ConfiguracaoInvalida("ligado precisa ser true ou false")
        with transacao(self._fabrica) as s:
            s.execute(insert(FeatureFlagDB).values(
                chave=chave, comerciante_id=comerciante_id, ligado=ligado, atualizado_por=autor,
            ).on_conflict_do_update(constraint="uq_feature_flags_chave",
                                    set_={"ligado": ligado, "atualizado_por": autor, "atualizado_em": _agora_sql()}))
        self._mudou()

    def remover_flag(self, chave: str, comerciante_id: int | None = None) -> None:
        with transacao(self._fabrica) as s:
            s.execute(delete(FeatureFlagDB).where(FeatureFlagDB.chave == chave, _comerciante(FeatureFlagDB,
                                                                                             comerciante_id)))
        self._mudou()

    def listar(self) -> dict[str, Any]:
        """Tudo para o admin: definição, padrão, valor global e sobreposições por comerciante."""
        self._cache.carregado_em = -1e9
        self._recarregar_se_preciso()
        config = []
        for chave, d in DEFINICOES.items():
            config.append({
                "chave": chave, "tipo": d.tipo, "descricao": d.descricao, "padrao": d.padrao,
                "opcoes": list(d.opcoes), "minimo": d.minimo, "maximo": d.maximo,
                "por_comerciante": d.por_comerciante, "global": self._cache.config.get((chave, None)),
                "comerciantes": {str(cid): v for (k, cid), v in self._cache.config.items()
                                 if k == chave and cid is not None},
            })
        flags = []
        for chave, d in FLAGS.items():
            flags.append({
                "chave": chave, "descricao": d.descricao, "padrao": d.padrao,
                "global": self._cache.flags.get((chave, None)),
                "comerciantes": {str(cid): v for (k, cid), v in self._cache.flags.items()
                                 if k == chave and cid is not None},
            })
        return {"versao": self.versao(), "configuracoes": config, "flags": flags}

    def _mudou(self) -> None:
        try:
            self._redis.incr(VERSAO)
        except Exception:
            log.exception("não consegui incrementar a versão das configurações")
        self._cache.carregado_em = -1e9
        self._cache.versao_vista_em = -1e9


def _comerciante(modelo, comerciante_id):
    return modelo.comerciante_id.is_(None) if comerciante_id is None else modelo.comerciante_id == comerciante_id


def _agora_sql():
    from sqlalchemy import func

    return func.now()


def para_json(valor: Any) -> str:
    return json.dumps(valor, ensure_ascii=False)
