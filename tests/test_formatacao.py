"""Testes da formatação no padrão brasileiro."""

from datetime import date
from decimal import Decimal

import pytest

from bcb_indicadores.formatacao import TRACO, data_br, numero_br, tabela


@pytest.mark.parametrize(
    ("valor", "casas", "sinal", "esperado"),
    [
        (Decimal("5.1149"), 4, False, "5,1149"),
        (Decimal("14"), 2, False, "14,00"),
        (Decimal("1234.5"), 2, False, "1.234,50"),
        (Decimal("0.125"), 2, False, "0,13"),  # arredonda para cima no meio
        (Decimal("0.05"), 2, True, "+0,05"),
        (Decimal("-1.25"), 2, True, "-1,25"),
        (Decimal("-0.001"), 2, True, "+0,00"),  # nunca "-0,00"
        (None, 2, False, TRACO),
    ],
)
def test_numero_br(valor, casas, sinal, esperado):
    assert numero_br(valor, casas, sinal=sinal) == esperado


def test_data_br():
    assert data_br(date(2026, 1, 5)) == "05/01/2026"
    assert data_br(None) == TRACO


def test_tabela_alinha_colunas():
    texto = tabela(["Nome", "Valor"], [["a", "1,00"], ["bbbb", "10,00"]], direita={1})
    assert texto.splitlines() == [
        "Nome  Valor",
        "----  -----",
        "a      1,00",
        "bbbb  10,00",
    ]
