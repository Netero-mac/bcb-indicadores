"""Testes da camada SQLite, com foco na idempotência da gravação."""

from datetime import date
from decimal import Decimal

import pytest

from bcb_indicadores import banco
from tests.conftest import obs

LOTE = [obs("2026-09-01", "5.1570"), obs("2026-09-02", "5.1273"), obs("2026-09-03", "5.0962")]


def test_gravar_insere_observacoes(conexao):
    resultado = banco.gravar(conexao, 1, LOTE)

    assert resultado == banco.ResultadoGravacao(inseridos=3, atualizados=0)
    assert banco.contar(conexao, 1) == 3


def test_gravar_duas_vezes_nao_duplica(conexao):
    banco.gravar(conexao, 1, LOTE)
    segunda = banco.gravar(conexao, 1, LOTE)

    assert segunda == banco.ResultadoGravacao(inseridos=0, atualizados=0)
    assert banco.contar(conexao, 1) == 3


def test_gravar_lote_sobreposto_insere_so_o_que_falta(conexao):
    banco.gravar(conexao, 1, LOTE[:2])
    resultado = banco.gravar(conexao, 1, LOTE)  # o 1º e o 2º já existem

    assert resultado == banco.ResultadoGravacao(inseridos=1, atualizados=0)
    assert banco.contar(conexao, 1) == 3


def test_gravar_valor_revisado_pelo_bcb_atualiza(conexao):
    banco.gravar(conexao, 433, [obs("2026-09-01", "0.82")])
    resultado = banco.gravar(conexao, 433, [obs("2026-09-01", "0.83")])

    assert resultado == banco.ResultadoGravacao(inseridos=0, atualizados=1)
    assert banco.listar(conexao, 433) == [obs("2026-09-01", "0.83")]


def test_mesma_data_em_series_diferentes_nao_conflita(conexao):
    banco.gravar(conexao, 1, [obs("2026-09-01", "5.1570")])
    banco.gravar(conexao, 12, [obs("2026-09-01", "0.051660")])

    assert banco.contar(conexao, 1) == 1
    assert banco.contar(conexao, 12) == 1


def test_preserva_casas_decimais(conexao):
    banco.gravar(conexao, 12, [obs("2026-09-01", "0.051660")])
    (valor,) = conexao.execute("SELECT valor FROM observacoes").fetchone()

    assert valor == "0.051660"
    assert banco.listar(conexao, 12)[0].valor == Decimal("0.051660")


def test_datas_gravadas_em_iso_para_ordenar_corretamente(conexao):
    # Em dd/mm/aaaa, "31/12/2025" > "02/01/2026" alfabeticamente; em ISO não.
    banco.gravar(conexao, 1, [obs("2026-01-02", "2"), obs("2025-12-31", "1")])

    assert [o.data for o in banco.listar(conexao, 1)] == [date(2025, 12, 31), date(2026, 1, 2)]
    assert banco.ultima_data(conexao, 1) == date(2026, 1, 2)


def test_ultima_data_de_serie_vazia(conexao):
    assert banco.ultima_data(conexao, 1) is None


def test_ultimos_em_ordem_cronologica(conexao):
    banco.gravar(conexao, 1, LOTE)

    assert banco.ultimos(conexao, 1, 2) == LOTE[1:]
    assert banco.ultimos(conexao, 1, 10) == LOTE


def test_listar_desde(conexao):
    banco.gravar(conexao, 1, LOTE)
    assert banco.listar(conexao, 1, desde=date(2026, 9, 2)) == LOTE[1:]


def test_abrir_banco_persiste_em_arquivo(tmp_path):
    caminho = tmp_path / "teste.db"
    with banco.abrir_banco(caminho) as conexao:
        banco.gravar(conexao, 1, LOTE)

    with banco.abrir_banco(caminho) as conexao:
        assert banco.contar(conexao, 1) == 3


def test_abrir_banco_desfaz_alteracoes_se_houver_erro(tmp_path):
    caminho = tmp_path / "teste.db"

    def gravar_e_falhar():
        with banco.abrir_banco(caminho) as conexao:
            banco.gravar(conexao, 1, LOTE)
            raise RuntimeError("falha no meio da operação")

    with pytest.raises(RuntimeError):
        gravar_e_falhar()

    with banco.abrir_banco(caminho) as conexao:
        assert banco.contar(conexao, 1) == 0
