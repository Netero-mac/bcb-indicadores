"""Testes do cliente da API: conversão de datas/valores e tratamento das respostas HTTP."""

from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock

import pytest
import requests

from bcb_indicadores.cliente import (
    STATUS_TEMPORARIOS,
    URL_BASE,
    ClienteSGS,
    ErroAPI,
    Observacao,
    RespostaSemConteudo,
    criar_sessao,
    formatar_data,
    parse_data,
    parse_resposta,
    parse_valor,
)
from tests.conftest import json_fixture, ler_fixture, resposta_http

# --- parse de datas ---------------------------------------------------------


def test_parse_data_formato_brasileiro():
    assert parse_data("01/09/2026") == date(2026, 9, 1)
    assert parse_data("31/12/1999") == date(1999, 12, 31)


def test_parse_data_nao_confunde_dia_e_mes():
    # 02/01 é 2 de janeiro, não 1º de fevereiro (padrão americano).
    assert parse_data("02/01/2026") == date(2026, 1, 2)


@pytest.mark.parametrize("texto", ["2026-09-01", "31/02/2026", "1/9/26", "", "abc"])
def test_parse_data_invalida(texto):
    with pytest.raises(ValueError, match="dd/mm/aaaa"):
        parse_data(texto)


def test_formatar_data_com_zeros_a_esquerda():
    assert formatar_data(date(2026, 1, 5)) == "05/01/2026"


# --- parse de valores -------------------------------------------------------


@pytest.mark.parametrize(
    ("texto", "esperado"),
    [
        ("5.1570", Decimal("5.1570")),
        ("14.00", Decimal("14.00")),
        ("-0.32", Decimal("-0.32")),
        ("0.051660", Decimal("0.051660")),
        (" 0.82 ", Decimal("0.82")),
    ],
)
def test_parse_valor_ponto_decimal(texto, esperado):
    valor = parse_valor(texto)
    assert valor == esperado
    assert str(valor) == str(esperado)  # preserva as casas decimais publicadas


@pytest.mark.parametrize("texto", ["1,5", "abc", "", "NaN", "Infinity"])
def test_parse_valor_invalido(texto):
    with pytest.raises(ValueError, match="inválido"):
        parse_valor(texto)


def test_parse_valor_sem_erro_de_ponto_flutuante():
    assert parse_valor("0.1") + parse_valor("0.2") == Decimal("0.3")


# --- parse da resposta completa ---------------------------------------------


def test_parse_resposta_gravada_do_dolar():
    observacoes = parse_resposta(json_fixture("sgs_1_dolar.json"))

    assert len(observacoes) == 7
    assert observacoes[0] == Observacao(date(2026, 9, 1), Decimal("5.1570"))
    assert observacoes[-1] == Observacao(date(2026, 9, 10), Decimal("5.1149"))
    # Fim de semana e feriado (05 a 07/09) não têm cotação.
    assert date(2026, 9, 7) not in {o.data for o in observacoes}


def test_parse_resposta_gravada_do_ipca_tem_valor_negativo():
    observacoes = parse_resposta(json_fixture("sgs_433_ipca.json"))
    assert len(observacoes) == 10
    assert Observacao(date(2026, 8, 1), Decimal("-0.32")) in observacoes


def test_parse_resposta_ordena_por_data():
    dados = [
        {"data": "03/01/2026", "valor": "3"},
        {"data": "01/01/2026", "valor": "1"},
        {"data": "02/01/2026", "valor": "2"},
    ]
    assert [o.valor for o in parse_resposta(dados)] == [1, 2, 3]


def test_parse_resposta_ignora_registro_sem_valor():
    dados = [{"data": "01/01/2026", "valor": ""}, {"data": "02/01/2026", "valor": "1.5"}]
    assert parse_resposta(dados) == [Observacao(date(2026, 1, 2), Decimal("1.5"))]


@pytest.mark.parametrize(
    "dados",
    [
        [{"data": "01/01/2026"}],  # sem a chave "valor"
        [{"data": "2026-01-01", "valor": "1"}],  # data fora do padrão
        [{"data": "01/01/2026", "valor": "1,5"}],  # vírgula decimal
        ["texto solto"],
    ],
)
def test_parse_resposta_registro_invalido(dados):
    with pytest.raises(ErroAPI, match="Registro inválido"):
        parse_resposta(dados)


def test_parse_resposta_que_nao_e_lista():
    with pytest.raises(ErroAPI, match="esperava uma lista"):
        parse_resposta({"erro": "algo"})


# --- ClienteSGS com a sessão HTTP simulada ----------------------------------


def cliente_com_resposta(resposta):
    sessao = MagicMock(spec=requests.Session)
    sessao.get.return_value = resposta
    return ClienteSGS(sessao=sessao, timeout=7), sessao


def test_buscar_monta_url_e_parametros_no_formato_da_api():
    cliente, sessao = cliente_com_resposta(resposta_http(200, ler_fixture("sgs_1_dolar.json")))

    observacoes = cliente.buscar(1, date(2026, 9, 1), date(2026, 9, 10))

    assert len(observacoes) == 7
    sessao.get.assert_called_once_with(
        URL_BASE.format(codigo=1),
        params={"formato": "json", "dataInicial": "01/09/2026", "dataFinal": "10/09/2026"},
        timeout=7,
    )


def test_buscar_periodo_sem_dados_devolve_lista_vazia():
    # Resposta real da API para um período sem cotações (fim de semana).
    cliente, _ = cliente_com_resposta(resposta_http(404, ler_fixture("sgs_vazio_404.json")))
    assert cliente.buscar(1, date(2026, 9, 6), date(2026, 9, 7)) == []


def test_buscar_janela_maior_que_10_anos_gera_erro_claro():
    # Resposta real da API (HTTP 406) para uma janela diária > 10 anos.
    cliente, _ = cliente_com_resposta(resposta_http(406, ler_fixture("sgs_erro_janela.json")))
    with pytest.raises(ErroAPI, match=r"HTTP 406.*no máximo, 10 anos"):
        cliente.buscar(1, date(2000, 1, 1), date(2015, 6, 1))


def test_buscar_304_sem_conteudo_gera_erro_especifico():
    cliente, _ = cliente_com_resposta(resposta_http(304))
    with pytest.raises(RespostaSemConteudo, match="304"):
        cliente.buscar(1, date(2000, 1, 1), date(2009, 12, 31))


def test_buscar_404_de_outro_tipo_e_erro():
    cliente, _ = cliente_com_resposta(resposta_http(404, b"<html>Pagina inexistente</html>"))
    with pytest.raises(ErroAPI, match="HTTP 404"):
        cliente.buscar(1, date(2026, 1, 1), date(2026, 1, 2))


def test_buscar_erro_500_apos_retentativas():
    cliente, _ = cliente_com_resposta(resposta_http(503, b"Service Unavailable"))
    with pytest.raises(ErroAPI, match="HTTP 503"):
        cliente.buscar(1, date(2026, 1, 1), date(2026, 1, 2))


def test_buscar_json_invalido():
    cliente, _ = cliente_com_resposta(resposta_http(200, b"<html>manutencao</html>"))
    with pytest.raises(ErroAPI, match="JSON válido"):
        cliente.buscar(1, date(2026, 1, 1), date(2026, 1, 2))


def test_buscar_falha_de_rede_vira_erro_api():
    sessao = MagicMock(spec=requests.Session)
    sessao.get.side_effect = requests.ConnectionError("sem rede")
    with pytest.raises(ErroAPI, match="Falha de comunicação"):
        ClienteSGS(sessao=sessao).buscar(1, date(2026, 1, 1), date(2026, 1, 2))


def test_buscar_periodo_invertido():
    cliente, sessao = cliente_com_resposta(resposta_http(200, b"[]"))
    with pytest.raises(ValueError, match="posterior"):
        cliente.buscar(1, date(2026, 1, 2), date(2026, 1, 1))
    sessao.get.assert_not_called()


def test_cliente_usa_timeout_por_padrao():
    assert ClienteSGS(sessao=MagicMock()).timeout == (5, 30)


# --- configuração das retentativas ------------------------------------------


def test_sessao_configura_retentativas_com_backoff():
    sessao = criar_sessao(tentativas=4, backoff=0.5)
    retry = sessao.get_adapter("https://api.bcb.gov.br").max_retries

    assert retry.total == 4
    assert retry.backoff_factor == 0.5
    assert set(STATUS_TEMPORARIOS) <= set(retry.status_forcelist)
    assert 404 not in retry.status_forcelist  # "sem dados" não deve ser repetido
    assert retry.allowed_methods == frozenset({"GET"})
    assert sessao.headers["User-Agent"].startswith("bcb-indicadores/")
