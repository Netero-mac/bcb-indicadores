"""Cálculos dos relatórios. Funções puras: recebem observações e devolvem números.

Definições usadas no resumo (sempre relativas à data da última observação, D):
- Último valor: a observação mais recente gravada.
- Variação 12m: último valor menos o valor vigente 12 meses antes, isto é, a
  última observação com data <= D - 12 meses. Para séries diárias, se D - 12
  meses cair num fim de semana ou feriado, vale o dia útil anterior.
- Média 12m: média aritmética simples das observações com data no intervalo
  (D - 12 meses, D]. Para o IPCA, são os 12 últimos meses; para séries
  diárias, os dias úteis do período.

Indicadores derivados (calculados a partir das séries gravadas):
- IPCA 12 meses: inflação composta de 12 meses consecutivos,
  produto de (1 + taxa/100) menos 1. Meses faltando no meio impedem o cálculo.
- Juro real: Selic meta descontada do IPCA 12 meses pela equação de Fisher,
  (1 + Selic/100) / (1 + IPCA12m/100) - 1. É um juro real "ex-post": compara a
  taxa de hoje com a inflação já ocorrida.
"""

from __future__ import annotations

import sqlite3
from bisect import bisect_right
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from bcb_indicadores import banco
from bcb_indicadores.cliente import Observacao
from bcb_indicadores.datas import somar_anos
from bcb_indicadores.series import DERIVADAS, Derivada, Serie, buscar_serie


@dataclass(frozen=True)
class ResumoSerie:
    serie: Serie | Derivada
    ultima: Observacao | None = None
    referencia_12m: Observacao | None = None  # valor vigente 12 meses antes
    variacao_12m: Decimal | None = None  # na unidade da série (p.p. ou R$)
    variacao_12m_pct: Decimal | None = None  # variação relativa, em %
    media_12m: Decimal | None = None
    observacoes_12m: int = 0


def data_de_corte(data: date) -> date:
    """Data exatamente 12 meses antes (29/02 vira 28/02 em ano não bissexto)."""
    return somar_anos(data, -1)


def valor_vigente_em(observacoes: list[Observacao], data: date) -> Observacao | None:
    """Última observação com data <= ``data``. Espera a lista em ordem cronológica."""
    candidata = None
    for obs in observacoes:
        if obs.data > data:
            break
        candidata = obs
    return candidata


def media(valores: list[Decimal]) -> Decimal | None:
    """Média aritmética simples; ``None`` para lista vazia."""
    if not valores:
        return None
    return sum(valores, Decimal(0)) / len(valores)


def variacao_percentual(atual: Decimal, anterior: Decimal) -> Decimal | None:
    """Variação relativa em %; indefinida (``None``) quando o valor anterior é zero."""
    if anterior == 0:
        return None
    return (atual / anterior - 1) * 100


def resumir(serie: Serie | Derivada, observacoes: list[Observacao]) -> ResumoSerie:
    """Calcula último valor, variação e média de 12 meses de uma série.

    ``observacoes`` deve estar em ordem cronológica (como devolvido por ``banco.listar``).
    """
    if not observacoes:
        return ResumoSerie(serie)

    ultima = observacoes[-1]
    corte = data_de_corte(ultima.data)

    referencia = valor_vigente_em(observacoes, corte)
    ultimos_12m = [o.valor for o in observacoes if corte < o.data <= ultima.data]

    variacao = variacao_pct = None
    if referencia is not None:
        variacao = ultima.valor - referencia.valor
        variacao_pct = variacao_percentual(ultima.valor, referencia.valor)

    return ResumoSerie(
        serie=serie,
        ultima=ultima,
        referencia_12m=referencia,
        variacao_12m=variacao,
        variacao_12m_pct=variacao_pct,
        media_12m=media(ultimos_12m),
        observacoes_12m=len(ultimos_12m),
    )


def resumo_do_banco(conexao: sqlite3.Connection, serie: Serie) -> ResumoSerie:
    """Lê do banco só o necessário (pouco mais de 12 meses) e calcula o resumo."""
    ultima = banco.ultima_data(conexao, serie.codigo)
    if ultima is None:
        return ResumoSerie(serie)
    # Margem de 40 dias antes do corte para achar o valor vigente 12 meses antes
    # mesmo em séries mensais ou após feriados prolongados.
    desde = data_de_corte(ultima) - timedelta(days=40)
    return resumir(serie, banco.listar(conexao, serie.codigo, desde=desde))


# ---------------------------------------------------------------------------
# Indicadores derivados
# ---------------------------------------------------------------------------


# Casas decimais dos indicadores derivados: mais que o suficiente para exibir
# e exportar, sem as 28 casas de ruído da divisão com Decimal.
PRECISAO_DERIVADOS = Decimal("0.000001")


def acumulado(taxas: Iterable[Decimal]) -> Decimal:
    """Taxa composta, em %, de uma sequência de taxas em %: [1, 1] -> 2,01."""
    fator = Decimal(1)
    for taxa in taxas:
        fator *= 1 + taxa / 100
    return (fator - 1) * 100


def juro_real(nominal: Decimal, inflacao: Decimal) -> Decimal:
    """Equação de Fisher, em %: (1 + nominal) / (1 + inflação) - 1."""
    return ((1 + nominal / 100) / (1 + inflacao / 100) - 1) * 100


def _mes(data: date) -> int:
    return data.year * 12 + data.month - 1


def ultimo_dia_do_mes(data: date) -> date:
    return (data.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)


def acumulado_12m(mensal: list[Observacao]) -> list[Observacao]:
    """Taxa mensal acumulada em 12 meses, um ponto por mês (data do último mês da janela).

    Serve para IPCA e IGP-M. ``mensal`` deve estar em ordem cronológica.
    Janelas com mês faltando são puladas.
    """
    saida = []
    for fim in range(11, len(mensal)):
        janela = mensal[fim - 11 : fim + 1]
        if _mes(janela[-1].data) - _mes(janela[0].data) != 11:
            continue
        valor = acumulado(o.valor for o in janela).quantize(PRECISAO_DERIVADOS)
        saida.append(Observacao(janela[-1].data, valor))
    return saida


def juro_real_mensal(selic: list[Observacao], ipca12m: list[Observacao]) -> list[Observacao]:
    """Juro real de cada mês: Selic vigente no fim do mês descontada do IPCA 12m daquele mês.

    Se houver Selic mais recente que o último IPCA divulgado, acrescenta um ponto
    final com a Selic mais recente e o último IPCA 12m (o número "de hoje").
    """
    datas = [o.data for o in selic]
    saida = []
    for inflacao in ipca12m:
        i = bisect_right(datas, ultimo_dia_do_mes(inflacao.data)) - 1
        # Exige uma Selic dentro do próprio mês, para não usar um valor antigo.
        if i < 0 or selic[i].data < inflacao.data:
            continue
        valor = juro_real(selic[i].valor, inflacao.valor).quantize(PRECISAO_DERIVADOS)
        saida.append(Observacao(inflacao.data, valor))

    if saida and selic[-1].data > ultimo_dia_do_mes(ipca12m[-1].data):
        valor = juro_real(selic[-1].valor, ipca12m[-1].valor).quantize(PRECISAO_DERIVADOS)
        saida.append(Observacao(selic[-1].data, valor))
    return saida


def derivadas_do_banco(conexao: sqlite3.Connection) -> dict[str, list[Observacao]]:
    """Calcula as séries derivadas a partir do banco, indexadas pelo apelido."""

    def listar(apelido: str) -> list[Observacao]:
        return banco.listar(conexao, buscar_serie(apelido).codigo)

    ipca12m = acumulado_12m(listar("ipca"))
    return {
        "ipca12m": ipca12m,
        "igpm12m": acumulado_12m(listar("igpm")),
        "juroreal": juro_real_mensal(listar("selic"), ipca12m),
    }


def observacoes_do_indicador(
    conexao: sqlite3.Connection, indicador: Serie | Derivada
) -> list[Observacao]:
    """Todas as observações de uma série do SGS ou de um indicador derivado."""
    if isinstance(indicador, Derivada):
        return derivadas_do_banco(conexao)[indicador.apelido]
    return banco.listar(conexao, indicador.codigo)


def resumos_derivados(conexao: sqlite3.Connection) -> list[ResumoSerie]:
    series = derivadas_do_banco(conexao)
    return [resumir(d, series[d.apelido]) for d in DERIVADAS]
