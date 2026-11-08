"""Gráfico do caixa por dia (PNG para o WhatsApp), desenhado com o Pillow.

Por que Pillow e não matplotlib: o matplotlib + numpy somam ~125 MB na função da Vercel (que já leva ffmpeg e
o cliente do Google); o Pillow tem ~7 MB e basta para barras e uma linha. Importado só quando é usado.

Forma (skill de dataviz): dois painéis com o mesmo eixo de dias, um eixo y cada (nunca dois eixos no mesmo
painel). Em cima, barras agrupadas por dia (vendas × saídas); embaixo, a linha do saldo acumulado.
Cores: as três primeiras da paleta categórica de referência, em ordem fixa (vendas, saídas, saldo).
Texto sempre nas cores de texto; legenda sempre presente; os totais vão também na legenda da mensagem.
"""

from decimal import Decimal
from io import BytesIO
from pathlib import Path

from app.domain.relatorios import RelatorioCaixa
from app.domain.tabelas import fmt_inteiro, fmt_rs

LARGURA = ALTURA = 1080
FUNDO = "#fcfcfb"
TEXTO = "#0b0b0b"
TEXTO_2 = "#52514e"
GRADE = "#e6e5e0"
EIXO = "#c9c8c2"
VENDAS = "#2a78d6"  # série 1 (azul)
SAIDAS = "#eb6834"  # série 2 (laranja)
SALDO = "#1baf7a"  # série 3 (água)
MARGEM = 64
LIMITE_BYTES = 5 * 1024 * 1024  # imagem no WhatsApp


FONTES = Path(__file__).parent / "fontes"  # Lato (licença OFL, em fontes/LICENCA_Lato_OFL.txt)


def _fonte(tamanho: int, negrito: bool = False):
    """A fonte embutida do Pillow não tem acentos ("Saídas"); por isso a Lato vai junto do código."""
    from PIL import ImageFont

    return ImageFont.truetype(str(FONTES / ("Lato-Bold.ttf" if negrito else "Lato-Regular.ttf")), tamanho)


def _escala(maximo: Decimal, minimo: Decimal = Decimal(0)) -> list[Decimal]:
    """Marcas "redondas" do eixo y (4 a 6), cobrindo de mínimo a máximo."""
    amplitude = max(maximo - minimo, Decimal(1))
    bruto = amplitude / 4
    potencia = Decimal(10) ** (len(str(int(bruto))) - 1)
    passo = next(p * potencia for p in (1, 2, 5, 10) if p * potencia >= bruto)
    inicio = (minimo / passo).to_integral_value(rounding="ROUND_FLOOR") * passo
    marcas = [inicio]
    while marcas[-1] < maximo:
        marcas.append(marcas[-1] + passo)
    return marcas


def _barra(d, x0: float, y_base: float, x1: float, y_topo: float, cor: str) -> None:
    """Barra com a ponta de dados arredondada e a base reta no eixo."""
    if y_base - y_topo < 1:
        return
    raio = min(8, (x1 - x0) / 2, (y_base - y_topo))
    d.rounded_rectangle([x0, y_topo, x1, y_base], radius=raio, fill=cor)
    d.rectangle([x0, max(y_topo + raio, y_base - raio), x1, y_base], fill=cor)


def dados_do_grafico(r: RelatorioCaixa) -> dict:
    """O que vai para o desenho (testável sem pixels): dias, séries e títulos."""
    acumulado, saldos = Decimal(0), []
    for d in r.dias:
        acumulado += d.saldo
        saldos.append(acumulado)
    return {
        "titulo": f"Caixa: {r.periodo.rotulo}",
        "subtitulo": f"Vendas {fmt_rs(r.vendas)} · Saídas {fmt_rs(r.saiu)} · Saldo {fmt_rs(r.saldo)}",
        "dias": [d.dia.day for d in r.dias],
        "vendas": [d.vendas for d in r.dias],
        "saidas": [d.saiu for d in r.dias],
        "saldo_acumulado": saldos,
    }


def legenda(r: RelatorioCaixa) -> str:
    """Os mesmos totais em texto, para quem não enxerga a imagem."""
    return (f"Caixa ({r.periodo.rotulo}): vendas {fmt_rs(r.vendas)}, saídas {fmt_rs(r.saiu)} "
            f"(compras, despesas e retiradas), saldo {fmt_rs(r.saldo)}.")


def desenhar(r: RelatorioCaixa) -> bytes:
    from PIL import Image, ImageDraw

    g = dados_do_grafico(r)
    img = Image.new("RGB", (LARGURA, ALTURA), FUNDO)
    d = ImageDraw.Draw(img)
    titulo, subtitulo, rotulo, pequeno = _fonte(46, True), _fonte(30), _fonte(28), _fonte(26)

    d.text((MARGEM, 48), g["titulo"], font=titulo, fill=TEXTO)
    d.text((MARGEM, 110), g["subtitulo"], font=subtitulo, fill=TEXTO_2)

    # legenda (sempre, com 2+ séries)
    x = MARGEM
    for cor, nome in ((VENDAS, "Vendas"), (SAIDAS, "Saídas (compras + gastos)")):
        d.rounded_rectangle([x, 168, x + 24, 192], radius=4, fill=cor)
        d.text((x + 34, 164), nome, font=rotulo, fill=TEXTO_2)
        x += 34 + int(d.textlength(nome, font=rotulo)) + 40

    n = len(g["dias"])
    eixo_x0 = MARGEM + 96
    eixo_x1 = LARGURA - MARGEM
    largura_dia = (eixo_x1 - eixo_x0) / max(n, 1)

    # painel 1: barras por dia
    topo1, base1 = 230, 660
    marcas = _escala(max([*g["vendas"], *g["saidas"], Decimal(1)]))
    maximo = marcas[-1]
    y_de = lambda v: base1 - float(v / maximo) * (base1 - topo1)  # noqa: E731
    for m in marcas:
        y = y_de(m)
        d.line([eixo_x0, y, eixo_x1, y], fill=GRADE if m else EIXO, width=2)
        txt = fmt_inteiro(m)
        d.text((eixo_x0 - 12 - d.textlength(txt, font=pequeno), y - 14), txt, font=pequeno, fill=TEXTO_2)
    folga = 2  # 2px de superfície entre barras vizinhas (em 1080px: ~6px)
    largura_barra = max(min((largura_dia - 6 * folga) / 2, 40), 3)
    for i, (venda, saida) in enumerate(zip(g["vendas"], g["saidas"])):
        centro = eixo_x0 + largura_dia * (i + 0.5)
        _barra(d, centro - largura_barra - folga * 1.5, base1, centro - folga * 1.5, y_de(venda), VENDAS)
        _barra(d, centro + folga * 1.5, base1, centro + largura_barra + folga * 1.5, y_de(saida), SAIDAS)

    # rótulos dos dias (no máximo ~10)
    salto = max(1, -(-n // 10))
    for i, dia in enumerate(g["dias"]):
        if i % salto == 0 or i == n - 1:
            txt = f"{dia:02d}"
            cx = eixo_x0 + largura_dia * (i + 0.5)
            d.text((cx - d.textlength(txt, font=pequeno) / 2, base1 + 10), txt, font=pequeno, fill=TEXTO_2)

    # painel 2: saldo acumulado
    topo2, base2 = 760, 1000
    d.text((MARGEM, 708), "Saldo acumulado", font=_fonte(28, True), fill=TEXTO)
    saldos = g["saldo_acumulado"]
    marcas2 = _escala(max([*saldos, Decimal(0)]), min([*saldos, Decimal(0)]))
    lo, hi = marcas2[0], marcas2[-1]
    y2 = lambda v: base2 - float((v - lo) / (hi - lo or 1)) * (base2 - topo2)  # noqa: E731
    for m in marcas2:
        d.line([eixo_x0, y2(m), eixo_x1, y2(m)], fill=EIXO if m == 0 else GRADE, width=2)
        txt = fmt_inteiro(m)
        d.text((eixo_x0 - 12 - d.textlength(txt, font=pequeno), y2(m) - 14), txt, font=pequeno, fill=TEXTO_2)
    pontos = [(eixo_x0 + largura_dia * (i + 0.5), y2(v)) for i, v in enumerate(saldos)]
    if len(pontos) > 1:
        d.line(pontos, fill=SALDO, width=6, joint="curve")
    fx, fy = pontos[-1]
    d.ellipse([fx - 9, fy - 9, fx + 9, fy + 9], fill=SALDO, outline=FUNDO, width=4)
    final = fmt_rs(saldos[-1])
    d.text((min(fx - d.textlength(final, font=pequeno), eixo_x1 - d.textlength(final, font=pequeno)),
            fy - 44 if fy - 44 > topo2 - 30 else fy + 14), final, font=pequeno, fill=TEXTO)

    saida = BytesIO()
    img.save(saida, format="PNG", optimize=True)
    return saida.getvalue()
