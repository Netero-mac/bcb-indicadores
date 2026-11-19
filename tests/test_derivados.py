"""Testes dos indicadores derivados: IPCA acumulado em 12 meses e juro real."""

from datetime import date
from decimal import Decimal

import pytest

from bcb_indicadores import banco
from bcb_indicadores.relatorios import (
    PRECISAO_DERIVADOS,
    acumulado,
    acumulado_12m,
    derivadas_do_banco,
    juro_real,
    juro_real_mensal,
    resumos_derivados,
    ultimo_dia_do_mes,
)
from tests.conftest import obs


def mensal(ano_inicio: int, mes_inicio: int, valores: list[str]):
    """IPCA mensal a partir de um mês: datas sempre no dia 1, como na API."""
    saida = []
    for i, valor in enumerate(valores):
        indice = ano_inicio * 12 + mes_inicio - 1 + i
        saida.append(obs(f"{indice // 12}-{indice % 12 + 1:02d}-01", valor))
    return saida


# --- funções puras ----------------------------------------------------------


def test_acumulado_e_composto_nao_somado():
    assert acumulado([Decimal("1"), Decimal("1")]) == Decimal("2.01")
    assert acumulado([Decimal("10"), Decimal("-10")]) == Decimal("-1")
    assert acumulado([]) == 0


def test_juro_real_pela_equacao_de_fisher():
    assert juro_real(Decimal("10"), Decimal("10")) == 0
    # 13,75% de Selic com 4,63% de inflação dão 8,72%, não 9,12% (subtração simples).
    assert juro_real(Decimal("13.75"), Decimal("4.63")).quantize(Decimal("0.01")) == Decimal("8.72")
    assert juro_real(Decimal("2"), Decimal("5")) < 0


@pytest.mark.parametrize(
    ("dia", "esperado"),
    [
        (date(2026, 1, 1), date(2026, 1, 31)),
        (date(2026, 2, 15), date(2026, 2, 28)),
        (date(2024, 2, 1), date(2024, 2, 29)),
        (date(2026, 12, 31), date(2026, 12, 31)),
    ],
)
def test_ultimo_dia_do_mes(dia, esperado):
    assert ultimo_dia_do_mes(dia) == esperado


def test_ipca_12m_precisa_de_12_meses():
    assert acumulado_12m(mensal(2025, 1, ["0.5"] * 11)) == []


def test_ipca_12m_janela_movel():
    serie = mensal(2025, 1, ["1"] * 12 + ["0"])
    resultado = acumulado_12m(serie)
    assert [o.data for o in resultado] == [date(2025, 12, 1), date(2026, 1, 1)]
    assert resultado[0].valor == Decimal("12.682503")  # composto: 1,01^12 - 1
    assert resultado[1].valor == acumulado([Decimal(1)] * 11).quantize(PRECISAO_DERIVADOS)


def test_ipca_12m_atravessa_a_virada_do_ano():
    resultado = acumulado_12m(mensal(2025, 11, ["0.1"] * 12))
    assert [o.data for o in resultado] == [date(2026, 10, 1)]


def test_ipca_12m_pula_janelas_com_mes_faltando():
    serie = mensal(2025, 1, ["1"] * 14)
    del serie[5]  # some junho/2025: nenhuma janela de 12 meses fica completa
    assert acumulado_12m(serie) == []


def test_juro_real_usa_selic_do_fim_do_mes_e_acrescenta_o_ponto_de_hoje():
    ipca = [obs("2026-09-01", "5"), obs("2026-10-01", "4")]
    selic = [
        obs("2026-09-10", "15"),
        obs("2026-09-30", "14"),  # vigente no fim de setembro
        obs("2026-10-31", "13"),  # vigente no fim de outubro
        obs("2026-11-19", "12"),  # mais recente que o último IPCA
    ]

    def esperado(selic, inflacao):
        return str(juro_real(Decimal(selic), Decimal(inflacao)).quantize(PRECISAO_DERIVADOS))

    assert juro_real_mensal(selic, ipca) == [
        obs("2026-09-01", esperado(14, 5)),
        obs("2026-10-01", esperado(13, 4)),
        obs("2026-11-19", esperado(12, 4)),
    ]


def test_juro_real_nao_usa_selic_antiga_de_outro_mes():
    ipca = [obs("2026-09-01", "5"), obs("2026-10-01", "4")]
    selic = [obs("2026-09-30", "14")]  # nada em outubro
    assert [o.data for o in juro_real_mensal(selic, ipca)] == [date(2026, 9, 1)]


def test_juro_real_sem_dados():
    assert juro_real_mensal([], []) == []
    assert juro_real_mensal([obs("2026-01-01", "10")], []) == []


# --- a partir do banco -------------------------------------------------------


def test_derivadas_do_banco(conexao):
    banco.gravar(conexao, 433, mensal(2025, 10, ["0.4"] * 13))
    banco.gravar(conexao, 432, [obs("2026-09-30", "15.00"), obs("2026-11-19", "14.00")])

    derivadas = derivadas_do_banco(conexao)
    assert [o.data for o in derivadas["ipca12m"]] == [date(2026, 9, 1), date(2026, 10, 1)]
    # Setembro tem Selic no mês; outubro não (pulado); o ponto final usa a Selic de 19/11.
    assert [o.data for o in derivadas["juroreal"]] == [date(2026, 9, 1), date(2026, 11, 19)]


def test_resumos_derivados_com_banco_vazio(conexao):
    resumos = resumos_derivados(conexao)
    assert [r.serie.apelido for r in resumos] == ["ipca12m", "igpm12m", "juroreal"]
    assert all(r.ultima is None for r in resumos)
