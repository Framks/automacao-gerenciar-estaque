"""Estoque dos comerciantes numa única planilha do Google Sheets, com abas por comerciante.

Abas:
- usuarios: um comerciante por linha (cadastro)
- <telefone>_estoque: visão atual, uma linha por produto
- <telefone>_movimentacoes: livro-caixa, só recebe linhas novas (fonte da verdade)
"""

import socket

from googleapiclient.errors import HttpError

from app.domain.models import Movimento, ProdutoEstoque, TipoMovimento
from app.domain.units import chave_produto
from app.errors import ServicoIndisponivel

ABA_USUARIOS = "usuarios"
CABECALHO_USUARIOS = ["telefone", "nome", "status", "criado_em"]
CABECALHO_ESTOQUE = ["produto", "unidade", "quantidade", "custo_medio", "atualizado_em"]
CABECALHO_MOVIMENTOS = [
    "id", "data_hora", "tipo", "produto", "quantidade", "unidade",
    "valor_total", "valor_unitario", "texto_transcrito", "whatsapp_msg_id", "referencia",
]
LINHAS_INICIAIS = 100  # a aba cresce sozinha quando o append passa disso


def abas_do_comerciante(telefone: str) -> dict[str, list[str]]:
    return {f"{telefone}_estoque": CABECALHO_ESTOQUE, f"{telefone}_movimentacoes": CABECALHO_MOVIMENTOS}


def intervalo(aba: str, a1: str) -> str:
    return f"'{aba}'!{a1}"


def executar(requisicao):
    """Executa uma chamada da API; limite de uso e falhas do Google viram ServicoIndisponivel (a fila repete)."""
    try:
        return requisicao.execute()
    except HttpError as e:
        if e.resp.status in (429, 500, 502, 503, 504):
            raise ServicoIndisponivel(f"Google Sheets: HTTP {e.resp.status}") from e
        raise
    except (TimeoutError, socket.timeout, ConnectionError) as e:
        raise ServicoIndisponivel(f"Google Sheets: {e}") from e


def garantir_abas(sheets, spreadsheet_id: str, abas: dict[str, list[str]]) -> None:
    """Cria (com cabeçalho) as abas que ainda não existem. Pode ser chamada de novo sem problema."""
    info = executar(sheets.spreadsheets().get(spreadsheetId=spreadsheet_id, fields="sheets.properties.title"))
    existentes = {s["properties"]["title"] for s in info.get("sheets", [])}
    faltando = {nome: cab for nome, cab in abas.items() if nome not in existentes}
    if not faltando:
        return
    executar(sheets.spreadsheets().batchUpdate(
        spreadsheetId=spreadsheet_id,
        body={"requests": [
            {"addSheet": {"properties": {
                "title": nome,
                "gridProperties": {"rowCount": LINHAS_INICIAIS, "columnCount": len(cab), "frozenRowCount": 1},
            }}}
            for nome, cab in faltando.items()
        ]},
    ))
    executar(sheets.spreadsheets().values().batchUpdate(
        spreadsheetId=spreadsheet_id,
        body={
            "valueInputOption": "RAW",
            "data": [{"range": intervalo(nome, "A1"), "values": [cab]} for nome, cab in faltando.items()],
        },
    ))


class SheetsInventoryRepository:
    def __init__(self, sheets, spreadsheet_id: str, telefone: str):
        self._values = sheets.spreadsheets().values()
        self._id = spreadsheet_id
        self._aba_estoque, self._aba_movimentos = abas_do_comerciante(telefone)

    def carregar_estoque(self) -> dict[str, ProdutoEstoque]:
        estoque = {}
        for i, linha in enumerate(self._ler(intervalo(self._aba_estoque, "A2:E"))):
            produto, unidade, quantidade, custo, atualizado = _completar(linha, 5)
            if not produto:
                continue
            estoque[chave_produto(str(produto))] = ProdutoEstoque(
                produto=str(produto),
                unidade=str(unidade or "un"),
                quantidade=_num(quantidade) or 0.0,
                custo_medio=_num(custo),
                atualizado_em=str(atualizado),
                linha=i + 2,
            )
        return estoque

    def carregar_movimentos(self) -> list[Movimento]:
        movimentos = []
        for linha in self._ler(intervalo(self._aba_movimentos, "A2:K")):
            (id_, data_hora, tipo, produto, qtd, unidade,
             valor, valor_un, texto, msg_id, ref) = _completar(linha, 11)
            if not id_:
                continue
            movimentos.append(
                Movimento(
                    id=str(id_), data_hora=str(data_hora), tipo=TipoMovimento(tipo), produto=str(produto),
                    quantidade=_num(qtd) or 0.0, unidade=str(unidade), valor_total=_num(valor),
                    valor_unitario=_num(valor_un), texto_transcrito=str(texto),
                    whatsapp_msg_id=str(msg_id), referencia=str(ref),
                )
            )
        return movimentos

    def salvar(self, movimentos: list[Movimento], estoque_alterado: list[ProdutoEstoque]) -> None:
        # 1) livro-caixa primeiro: se algo falhar depois, o histórico já está garantido
        if movimentos:
            self._anexar(self._aba_movimentos, [_linha_movimento(m) for m in movimentos])

        existentes = [p for p in estoque_alterado if p.linha]
        novos = [p for p in estoque_alterado if not p.linha]
        if existentes:
            executar(self._values.batchUpdate(
                spreadsheetId=self._id,
                body={
                    "valueInputOption": "RAW",
                    "data": [
                        {"range": intervalo(self._aba_estoque, f"A{p.linha}:E{p.linha}"), "values": [_linha_estoque(p)]}
                        for p in existentes
                    ],
                },
            ))
        if novos:
            self._anexar(self._aba_estoque, [_linha_estoque(p) for p in novos])

    def _ler(self, faixa: str) -> list[list]:
        resposta = executar(self._values.get(
            spreadsheetId=self._id, range=faixa, valueRenderOption="UNFORMATTED_VALUE"
        ))
        return resposta.get("values", [])

    def _anexar(self, aba: str, linhas: list[list]) -> None:
        executar(self._values.append(
            spreadsheetId=self._id,
            range=intervalo(aba, "A1"),
            valueInputOption="RAW",  # RAW: telefone e data ficam como texto, sem conversão do Sheets
            insertDataOption="INSERT_ROWS",
            body={"values": linhas},
        ))


def _linha_estoque(p: ProdutoEstoque) -> list:
    custo = round(p.custo_medio, 4) if p.custo_medio is not None else ""
    return [p.produto, p.unidade, round(p.quantidade, 3), custo, p.atualizado_em]


def _linha_movimento(m: Movimento) -> list:
    return [
        m.id, m.data_hora, m.tipo.value, m.produto, round(m.quantidade, 3), m.unidade,
        _vazio(m.valor_total), _vazio(m.valor_unitario), m.texto_transcrito, m.whatsapp_msg_id, m.referencia,
    ]


def _vazio(valor: float | None):
    return "" if valor is None else round(valor, 4)


def _completar(linha: list, tamanho: int) -> list:
    return list(linha) + [""] * (tamanho - len(linha))


def _num(valor) -> float | None:
    if valor in ("", None):
        return None
    if isinstance(valor, (int, float)):
        return float(valor)
    return float(str(valor).replace(",", "."))
