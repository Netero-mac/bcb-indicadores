"""Cálculos dos relatórios. Funções puras: recebem observações e devolvem números.

Definições usadas no resumo (sempre relativas à data da última observação, D):
- Último valor: a observação mais recente gravada.
- Variação 12m: último valor menos o valor vigente 12 meses antes, isto é, a
  última observação com data <= D - 12 meses. Para séries diárias, se D - 12
  meses cair num fim de semana ou feriado, vale o dia útil anterior.
- Média 12m: média aritmética simples das observações com data no intervalo
  (D - 12 meses, D]. Para o IPCA, são os 12 últimos meses; para séries
  diárias, os dias úteis do período.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from bcb_indicadores import banco
from bcb_indicadores.cliente import Observacao
from bcb_indicadores.datas import somar_anos
from bcb_indicadores.series import Serie


@dataclass(frozen=True)
class ResumoSerie:
    serie: Serie
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


def resumir(serie: Serie, observacoes: list[Observacao]) -> ResumoSerie:
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
