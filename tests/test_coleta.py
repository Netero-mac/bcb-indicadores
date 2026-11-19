"""Testes da coleta: janelas de 10 anos, coleta incremental e idempotente."""

from datetime import date, timedelta
from itertools import pairwise

import pytest

from bcb_indicadores import banco
from bcb_indicadores.cliente import ErroAPI, RespostaSemConteudo
from bcb_indicadores.coleta import (
    DATA_INICIAL_PADRAO,
    buscar_janela,
    coletar_serie,
    janelas,
)
from bcb_indicadores.datas import somar_anos
from bcb_indicadores.series import buscar_serie
from tests.conftest import ClienteFalso, obs

DOLAR = buscar_serie("dolar")
HOJE = date(2026, 11, 19)

# --- janelas ----------------------------------------------------------------


def test_janelas_dividem_25_anos_em_3():
    assert list(janelas(date(2000, 1, 1), date(2024, 12, 31))) == [
        (date(2000, 1, 1), date(2009, 12, 31)),
        (date(2010, 1, 1), date(2019, 12, 31)),
        (date(2020, 1, 1), date(2024, 12, 31)),
    ]


def test_janela_unica_quando_periodo_curto():
    assert list(janelas(date(2026, 9, 1), date(2026, 9, 10))) == [
        (date(2026, 9, 1), date(2026, 9, 10))
    ]


def test_janela_de_um_dia():
    assert list(janelas(HOJE, HOJE)) == [(HOJE, HOJE)]


def test_periodo_invertido_nao_gera_janelas():
    assert list(janelas(date(2026, 1, 2), date(2026, 1, 1))) == []


@pytest.mark.parametrize(
    ("inicio", "fim"),
    [
        (date(1999, 3, 15), date(2026, 11, 19)),
        (date(2004, 2, 29), date(2031, 5, 1)),  # começa num 29/02
        (date(2016, 1, 1), date(2025, 12, 31)),  # exatamente 10 anos
    ],
)
def test_janelas_cobrem_o_periodo_sem_buracos_nem_sobreposicao(inicio, fim):
    resultado = list(janelas(inicio, fim))

    assert resultado[0][0] == inicio
    assert resultado[-1][1] == fim
    for (_, fim_anterior), (inicio_seguinte, _) in pairwise(resultado):
        assert inicio_seguinte == fim_anterior + timedelta(days=1)
    for ini, f in resultado:
        assert f < somar_anos(ini, 10)  # nunca alcança 10 anos completos + 1 dia


# --- buscar_janela (divisão em caso de 304) ----------------------------------


class ClienteCom304(ClienteFalso):
    """Responde 304 para janelas que contenham a data "amaldiçoada"."""

    def __init__(self, dados, maior_janela_aceita_dias):
        super().__init__(dados)
        self.limite = maior_janela_aceita_dias

    def buscar(self, codigo, inicio, fim):
        if (fim - inicio).days > self.limite:
            self.chamadas.append((codigo, inicio, fim))
            raise RespostaSemConteudo("304")
        return super().buscar(codigo, inicio, fim)


def test_buscar_janela_divide_ao_receber_304():
    dados = {1: [obs("2001-06-01", "1.0"), obs("2008-03-03", "2.0")]}
    cliente = ClienteCom304(dados, maior_janela_aceita_dias=2000)

    resultado = buscar_janela(cliente, 1, date(2000, 1, 1), date(2009, 12, 31))

    assert resultado == dados[1]
    assert len(cliente.chamadas) == 3  # 1 com 304 + 2 metades


def test_buscar_janela_desiste_depois_do_limite_de_divisoes():
    cliente = ClienteCom304({}, maior_janela_aceita_dias=-1)  # sempre 304
    with pytest.raises(RespostaSemConteudo):
        buscar_janela(cliente, 1, date(2000, 1, 1), date(2009, 12, 31), divisoes=2)
    # janela inteira -> metade esquerda -> quarto esquerdo (sem divisões restantes): desiste.
    assert len(cliente.chamadas) == 3


def test_buscar_janela_nao_divide_em_outros_erros():
    class ClienteForaDoAr(ClienteFalso):
        def buscar(self, codigo, inicio, fim):
            self.chamadas.append((codigo, inicio, fim))
            raise ErroAPI("HTTP 503")

    cliente = ClienteForaDoAr({})
    with pytest.raises(ErroAPI, match="503"):
        buscar_janela(cliente, 1, date(2000, 1, 1), date(2009, 12, 31))
    assert len(cliente.chamadas) == 1


# --- coletar_serie ----------------------------------------------------------

HISTORICO = [
    obs("2001-05-02", "2.2000"),
    obs("2015-07-01", "3.1000"),
    obs("2026-09-01", "5.1570"),
    obs("2026-09-02", "5.1273"),
]


def test_primeira_coleta_baixa_desde_a_data_padrao_em_janelas(conexao):
    cliente = ClienteFalso({1: HISTORICO})

    resultado = coletar_serie(conexao, cliente, DOLAR, hoje=HOJE)

    assert resultado.inicio == DATA_INICIAL_PADRAO
    assert resultado.inseridos == 4
    assert [c[1:] for c in cliente.chamadas] == list(janelas(DATA_INICIAL_PADRAO, HOJE))
    assert len(cliente.chamadas) == 3


def test_coletar_duas_vezes_nao_duplica(conexao):
    cliente = ClienteFalso({1: HISTORICO})

    coletar_serie(conexao, cliente, DOLAR, hoje=HOJE)
    segunda = coletar_serie(conexao, cliente, DOLAR, hoje=HOJE)

    assert segunda.inseridos == 0
    assert segunda.atualizados == 0
    assert banco.contar(conexao, DOLAR.codigo) == 4


def test_segunda_coleta_e_incremental(conexao):
    cliente = ClienteFalso({1: HISTORICO})
    coletar_serie(conexao, cliente, DOLAR, hoje=date(2026, 9, 2))

    cliente.dados[1] = [*HISTORICO, obs("2026-09-03", "5.0962")]
    cliente.chamadas.clear()
    resultado = coletar_serie(conexao, cliente, DOLAR, hoje=date(2026, 9, 3))

    # Recomeça da última data gravada, em uma única requisição.
    assert cliente.chamadas == [(1, date(2026, 9, 2), date(2026, 9, 3))]
    assert resultado.recebidos == 2
    assert resultado.inseridos == 1
    assert banco.contar(conexao, DOLAR.codigo) == 5


def test_coleta_respeita_desde_e_ate(conexao):
    cliente = ClienteFalso({1: HISTORICO})

    resultado = coletar_serie(
        conexao, cliente, DOLAR, desde=date(2026, 1, 1), ate=date(2026, 9, 1), hoje=HOJE
    )

    assert cliente.chamadas == [(1, date(2026, 1, 1), date(2026, 9, 1))]
    assert resultado.inseridos == 1


def test_coleta_com_desde_no_futuro_falha(conexao):
    with pytest.raises(ValueError, match="posterior"):
        coletar_serie(conexao, ClienteFalso({}), DOLAR, desde=date(2027, 1, 1), hoje=HOJE)
