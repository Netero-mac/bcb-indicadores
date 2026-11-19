"""Testes dos cálculos do resumo: último valor, variação e média de 12 meses."""

from datetime import date
from decimal import Decimal

import pytest

from bcb_indicadores import banco
from bcb_indicadores.cliente import parse_resposta
from bcb_indicadores.relatorios import (
    ResumoSerie,
    data_de_corte,
    media,
    resumir,
    resumo_do_banco,
    valor_vigente_em,
    variacao_percentual,
)
from bcb_indicadores.series import buscar_serie
from tests.conftest import json_fixture, obs

IPCA = buscar_serie("ipca")
DOLAR = buscar_serie("dolar")
SELIC = buscar_serie("selic")


# --- funções auxiliares -----------------------------------------------------


def test_data_de_corte_doze_meses_antes():
    assert data_de_corte(date(2026, 9, 10)) == date(2025, 9, 10)


def test_data_de_corte_em_29_de_fevereiro():
    assert data_de_corte(date(2024, 2, 29)) == date(2023, 2, 28)


def test_media_exata_com_decimal():
    assert media([Decimal("0.1"), Decimal("0.2")]) == Decimal("0.15")
    assert media([]) is None


def test_variacao_percentual():
    assert variacao_percentual(Decimal("5.5"), Decimal("5")) == Decimal("10")
    assert variacao_percentual(Decimal("4.5"), Decimal("5")) == Decimal("-10")
    assert variacao_percentual(Decimal("1"), Decimal("0")) is None


def test_valor_vigente_usa_o_dia_util_anterior():
    observacoes = [obs("2025-09-05", "5.40"), obs("2025-09-08", "5.45")]
    # 06 e 07/09/2025 foram sábado e domingo: vale a cotação de sexta (05/09).
    assert valor_vigente_em(observacoes, date(2025, 9, 7)) == observacoes[0]
    assert valor_vigente_em(observacoes, date(2025, 9, 8)) == observacoes[1]
    assert valor_vigente_em(observacoes, date(2025, 9, 4)) is None


# --- resumir ----------------------------------------------------------------

# IPCA de set a dez/2025 (valores ilustrativos) + resposta gravada da API (jan a out/2026).
IPCA_2025 = [
    obs("2025-09-01", "0.48"),
    obs("2025-10-01", "0.09"),
    obs("2025-11-01", "0.18"),
    obs("2025-12-01", "0.33"),
]
IPCA_GRAVADO = parse_resposta(json_fixture("sgs_433_ipca.json"))


def test_resumo_do_ipca():
    resumo = resumir(IPCA, IPCA_2025 + IPCA_GRAVADO)

    assert resumo.ultima == obs("2026-10-01", "0.14")
    assert resumo.referencia_12m == obs("2025-10-01", "0.09")
    assert resumo.variacao_12m == Decimal("0.05")  # 0,14 - 0,09 (p.p.)
    # Média de nov/2025 a out/2026: 12 meses, sem incluir out/2025.
    doze_meses = [Decimal(v) for v in ("0.18", "0.33")] + [o.valor for o in IPCA_GRAVADO]
    assert resumo.observacoes_12m == 12
    assert resumo.media_12m == sum(doze_meses) / 12
    assert round(resumo.media_12m, 4) == Decimal("0.3783")  # (0,18 + 0,33 + 4,03) / 12


def test_resumo_de_serie_diaria_com_referencia_em_fim_de_semana():
    observacoes = [
        obs("2025-09-05", "5.4000"),  # lacuna proposital de 06 a 10/09: o corte (10/09/2025)
        obs("2025-09-11", "5.4500"),  # usa o último valor anterior, de 05/09
        obs("2026-03-02", "5.2000"),
        obs("2026-09-10", "5.9400"),
    ]

    resumo = resumir(DOLAR, observacoes)

    assert resumo.referencia_12m == obs("2025-09-05", "5.4000")
    assert resumo.variacao_12m == Decimal("0.5400")
    assert resumo.variacao_12m_pct == Decimal("10")
    # Média só do que está em (10/09/2025, 10/09/2026].
    assert resumo.observacoes_12m == 3
    assert resumo.media_12m == (Decimal("5.45") + Decimal("5.20") + Decimal("5.94")) / 3


def test_resumo_com_menos_de_12_meses_de_historico():
    observacoes = [obs("2026-06-01", "15.00"), obs("2026-09-01", "14.00")]

    resumo = resumir(SELIC, observacoes)

    assert resumo.ultima == obs("2026-09-01", "14.00")
    assert resumo.referencia_12m is None
    assert resumo.variacao_12m is None
    assert resumo.variacao_12m_pct is None
    assert resumo.media_12m == Decimal("14.5")


def test_resumo_de_serie_vazia():
    assert resumir(SELIC, []) == ResumoSerie(SELIC)


def test_resumo_valor_anterior_zero_nao_divide_por_zero():
    resumo = resumir(IPCA, [obs("2025-01-01", "0"), obs("2026-01-01", "0.33")])
    assert resumo.variacao_12m == Decimal("0.33")
    assert resumo.variacao_12m_pct is None


@pytest.mark.parametrize("serie", [IPCA, DOLAR])
def test_resumo_do_banco_igual_ao_calculo_com_historico_completo(conexao, serie):
    observacoes = IPCA_2025 + IPCA_GRAVADO
    banco.gravar(conexao, serie.codigo, [obs("2010-01-01", "9.99"), *observacoes])

    assert resumo_do_banco(conexao, serie) == resumir(serie, observacoes)


def test_resumo_do_banco_vazio(conexao):
    assert resumo_do_banco(conexao, SELIC) == ResumoSerie(SELIC)
