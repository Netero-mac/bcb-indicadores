"""Testes da interface web: servidor real numa porta livre, banco temporário e cliente falso."""

import threading

import pytest
import requests

from bcb_indicadores import banco
from bcb_indicadores.cli import main
from bcb_indicadores.web import CABECALHO_ACAO, Aplicacao, criar_servidor
from tests.conftest import ClienteFalso, obs

DADOS = {
    432: [obs("2025-09-10", "15.00"), obs("2026-09-10", "14.00")],
    1: [obs("2025-09-10", "5.4000"), obs("2026-09-10", "5.9400")],
}


@pytest.fixture
def caminho_banco(tmp_path):
    return tmp_path / "teste.db"


@pytest.fixture
def cliente():
    return ClienteFalso(DADOS)


@pytest.fixture
def url(caminho_banco, cliente):
    servidor = criar_servidor(Aplicacao(caminho_banco, lambda: cliente), porta=0)
    thread = threading.Thread(target=servidor.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{servidor.server_address[1]}"
    servidor.shutdown()
    servidor.server_close()


def gravar_dados(caminho_banco):
    with banco.abrir_banco(caminho_banco) as conexao:
        for codigo, observacoes in DADOS.items():
            banco.gravar(conexao, codigo, observacoes)


def coletar(url):
    return requests.post(f"{url}/api/coletar", headers={CABECALHO_ACAO: "1"}, timeout=10)


def test_pagina_inicial(url):
    r = requests.get(url, timeout=5)
    assert r.status_code == 200
    assert r.headers["Content-Type"].startswith("text/html")
    assert "Indicadores <em>econômicos</em>" in r.text


def test_resumo_com_banco_vazio(url):
    dados = requests.get(f"{url}/api/resumo", timeout=5).json()
    assert [s["apelido"] for s in dados["series"]] == ["selic", "ipca", "dolar", "cdi"]
    assert all(s["ultima"] is None for s in dados["series"])


def test_resumo_formatado_no_padrao_brasileiro(url, caminho_banco):
    gravar_dados(caminho_banco)
    series = {
        s["apelido"]: s for s in requests.get(f"{url}/api/resumo", timeout=5).json()["series"]
    }

    selic = series["selic"]
    assert selic["ultima"] == {"data": "2026-09-10", "data_br": "10/09/2026", "valor_br": "14,00"}
    assert selic["variacao_12m"] == "-1,00 p.p."
    assert selic["sinal_variacao"] == -1
    assert selic["total"] == 2

    dolar = series["dolar"]
    assert dolar["variacao_12m"] == "+0,5400 (+10,00%)"
    assert dolar["sinal_variacao"] == 1


def test_observacoes_da_serie(url, caminho_banco):
    gravar_dados(caminho_banco)
    dados = requests.get(f"{url}/api/series/dolar", timeout=5).json()
    assert dados["observacoes"] == [["2025-09-10", "5.4000"], ["2026-09-10", "5.9400"]]


def test_serie_por_codigo(url, caminho_banco):
    gravar_dados(caminho_banco)
    assert requests.get(f"{url}/api/series/432", timeout=5).json()["apelido"] == "selic"


def test_csv_da_serie(url, caminho_banco):
    gravar_dados(caminho_banco)
    r = requests.get(f"{url}/api/series/selic.csv", timeout=5)
    assert r.status_code == 200
    assert 'filename="selic.csv"' in r.headers["Content-Disposition"]
    assert r.text.splitlines() == ["data,valor", "2025-09-10,15.00", "2026-09-10,14.00"]


def test_serie_desconhecida(url):
    r = requests.get(f"{url}/api/series/euro", timeout=5)
    assert r.status_code == 404
    assert "Série desconhecida" in r.json()["erro"]


def test_rota_inexistente(url):
    assert requests.get(f"{url}/nada", timeout=5).status_code == 404


def test_coletar_grava_no_banco(url, cliente):
    r = coletar(url)
    assert r.status_code == 200
    resultados = {item["nome"]: item for item in r.json()["resultados"]}
    assert resultados["Selic meta"]["inseridos"] == 2
    assert resultados["IPCA mensal"]["inseridos"] == 0
    assert {codigo for codigo, *_ in cliente.chamadas} == {432, 433, 1, 12}

    serie = requests.get(f"{url}/api/series/selic", timeout=5).json()
    assert len(serie["observacoes"]) == 2


def test_coletar_informa_erro_de_uma_serie(caminho_banco):
    from bcb_indicadores.cliente import ErroAPI

    class ClienteInstavel(ClienteFalso):
        def buscar(self, codigo, inicio, fim):
            if codigo == 433:
                raise ErroAPI("HTTP 503 simulado")
            return super().buscar(codigo, inicio, fim)

    app = Aplicacao(caminho_banco, lambda: ClienteInstavel(DADOS))
    resultados = {item["nome"]: item for item in app.coletar()["resultados"]}
    assert resultados["IPCA mensal"] == {"nome": "IPCA mensal", "erro": "HTTP 503 simulado"}
    assert resultados["Selic meta"]["inseridos"] == 2


def test_coletar_recusa_sem_cabecalho(url, cliente):
    r = requests.post(f"{url}/api/coletar", timeout=5)
    assert r.status_code == 403
    assert cliente.chamadas == []


def test_coletar_recusa_coleta_simultanea(caminho_banco, cliente):
    app = Aplicacao(caminho_banco, lambda: cliente)
    app._coletando.acquire()
    try:
        assert app.coletar() is None
    finally:
        app._coletando.release()
    assert app.coletar() is not None


def test_recusa_host_externo(url):
    r = requests.get(f"{url}/api/resumo", headers={"Host": "evil.example"}, timeout=5)
    assert r.status_code == 403


def test_cli_web_porta_invalida(capsys):
    with pytest.raises(SystemExit):
        main(["web", "--porta", "abc"])
    assert "porta inválida" in capsys.readouterr().err


def test_cli_web_porta_ocupada(tmp_path, capsys, monkeypatch):
    # Simula a falha em vez de ocupar uma porta de verdade: no Windows, SO_REUSEADDR
    # permitiria abrir dois servidores na mesma porta e o teste ficaria instável.
    from bcb_indicadores import web

    def falhar(*_args, **_kwargs):
        raise OSError(98, "Address already in use")

    monkeypatch.setattr(web, "servir", falhar)
    assert main(["--banco", str(tmp_path / "x.db"), "web", "--porta", "8000"]) == 1
    assert "Tente outra porta" in capsys.readouterr().err
