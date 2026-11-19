"""Testes de ponta a ponta da CLI, com banco temporário e cliente falso (sem internet)."""

import csv
from datetime import date

import pytest

from bcb_indicadores.cli import main
from bcb_indicadores.cliente import ErroAPI, parse_resposta
from tests.conftest import ClienteFalso, json_fixture, obs

DADOS = {
    432: [obs("2025-09-10", "15.00"), obs("2026-09-10", "14.00")],
    433: parse_resposta(json_fixture("sgs_433_ipca.json")),
    1: [obs("2025-09-10", "5.4000"), *parse_resposta(json_fixture("sgs_1_dolar.json"))],
    12: [obs("2026-09-10", "0.051660")],
}
PERIODO = ["--desde", "01/01/2025", "--ate", "31/10/2026"]


@pytest.fixture
def db(tmp_path):
    return str(tmp_path / "teste.db")


@pytest.fixture
def cliente():
    return ClienteFalso(DADOS)


def rodar(db, *args, cliente=None):
    return main(["--banco", db, *args], cliente=cliente)


def test_coletar_e_idempotente(db, cliente, capsys):
    assert rodar(db, "coletar", *PERIODO, cliente=cliente) == 0
    primeira = capsys.readouterr().out
    assert "Coletando Dólar PTAX venda (1)" in primeira
    assert "8 recebidos, 8 novos, 0 atualizados" in primeira

    assert rodar(db, "coletar", *PERIODO, cliente=cliente) == 0
    segunda = capsys.readouterr().out
    assert "8 recebidos, 0 novos, 0 atualizados" in segunda


def test_coletar_apenas_algumas_series(db, cliente):
    assert rodar(db, "coletar", "--series", "selic", "433", *PERIODO, cliente=cliente) == 0
    assert {codigo for codigo, *_ in cliente.chamadas} == {432, 433}


def test_coletar_continua_quando_uma_serie_falha(db, capsys):
    class ClienteInstavel(ClienteFalso):
        def buscar(self, codigo, inicio, fim):
            if codigo == 433:
                raise ErroAPI("HTTP 503 simulado")
            return super().buscar(codigo, inicio, fim)

    assert rodar(db, "coletar", *PERIODO, cliente=ClienteInstavel(DADOS)) == 1
    saida = capsys.readouterr()
    assert "HTTP 503 simulado" in saida.err

    # As outras séries foram gravadas mesmo assim.
    assert rodar(db, "ultimos", "selic") == 0


def test_ultimos(db, cliente, capsys):
    rodar(db, "coletar", *PERIODO, cliente=cliente)
    capsys.readouterr()

    assert rodar(db, "ultimos", "dolar", "--n", "3") == 0
    linhas = capsys.readouterr().out.splitlines()
    assert linhas[0] == "Dólar PTAX venda (código 1) — em R$"
    assert linhas[-3:] == ["08/09/2026  5,0856", "09/09/2026  5,0979", "10/09/2026  5,1149"]


def test_resumo(db, cliente, capsys):
    rodar(db, "coletar", *PERIODO, cliente=cliente)
    capsys.readouterr()

    assert rodar(db, "resumo") == 0
    saida = capsys.readouterr().out
    linhas = {linha.split("  ")[0]: linha for linha in saida.splitlines()}

    assert linhas["Selic meta"].split() == [
        "Selic", "meta", "%", "a.a.", "10/09/2026", "14,00", "-1,00", "p.p.", "14,00",
    ]  # fmt: skip
    assert "5,1149" in linhas["Dólar PTAX venda"]
    assert "-0,2851 (-5,28%)" in linhas["Dólar PTAX venda"]
    assert "0,051660" in linhas["CDI diário"]
    assert "0,14" in linhas["IPCA mensal"]
    # Indicadores derivados aparecem no fim (o fixture só tem 8 meses de IPCA: sem 12m).
    assert linhas["IPCA 12 meses"].split()[-4:] == ["—"] * 4
    assert "Juro real" in linhas


def test_resumo_com_banco_vazio(db, capsys):
    assert rodar(db, "resumo") == 1
    assert "Banco vazio" in capsys.readouterr().err


def test_exportar_csv(db, cliente, tmp_path, capsys):
    rodar(db, "coletar", *PERIODO, cliente=cliente)
    arquivo = tmp_path / "ipca.csv"

    assert rodar(db, "exportar", "ipca", "--csv", str(arquivo)) == 0
    assert "10 linhas de IPCA mensal exportadas" in capsys.readouterr().out

    with arquivo.open(encoding="utf-8", newline="") as f:
        linhas = list(csv.reader(f))
    assert linhas[0] == ["data", "valor"]
    assert linhas[1] == ["2026-01-01", "0.33"]
    assert linhas[8] == ["2026-08-01", "-0.32"]
    assert len(linhas) == 11


def test_exportar_serie_sem_dados(db, tmp_path, capsys):
    assert rodar(db, "exportar", "cdi", "--csv", str(tmp_path / "x.csv")) == 1
    assert "coletar --series cdi" in capsys.readouterr().err
    assert not (tmp_path / "x.csv").exists()


@pytest.mark.parametrize(
    ("args", "mensagem"),
    [
        (["ultimos", "libra"], "Série desconhecida"),
        (["ultimos", "selic", "--n", "0"], "inteiro positivo"),
        (["coletar", "--desde", "2026-01-01"], "use dd/mm/aaaa"),
        (["exportar", "selic"], "--csv"),
    ],
)
def test_argumentos_invalidos(db, args, mensagem, capsys):
    with pytest.raises(SystemExit) as saida:
        rodar(db, *args)
    assert saida.value.code == 2
    assert mensagem in capsys.readouterr().err


def test_banco_pela_variavel_de_ambiente(tmp_path, monkeypatch, cliente):
    caminho = tmp_path / "via_env.db"
    monkeypatch.setenv("BCB_INDICADORES_DB", str(caminho))

    assert main(["coletar", "--series", "cdi", *PERIODO], cliente=cliente) == 0
    assert caminho.exists()


def test_coleta_padrao_vai_ate_hoje(db, cliente):
    rodar(db, "coletar", "--series", "cdi", "--desde", "01/01/2026", cliente=cliente)
    assert cliente.chamadas[-1][2] == date.today()


def test_exportar_com_periodo(db, cliente, tmp_path, capsys):
    rodar(db, "coletar", *PERIODO, cliente=cliente)
    destino = tmp_path / "selic.csv"
    assert rodar(db, "exportar", "selic", "--csv", str(destino), "--desde", "01/01/2026") == 0
    assert destino.read_text().splitlines() == ["data,valor", "2026-09-10,14.00"]
    assert "1 linhas" in capsys.readouterr().out


def test_exportar_periodo_vazio_ou_invertido(db, cliente, tmp_path, capsys):
    rodar(db, "coletar", *PERIODO, cliente=cliente)
    destino = str(tmp_path / "x.csv")
    capsys.readouterr()
    assert rodar(db, "exportar", "selic", "--csv", destino, "--desde", "01/01/2030") == 1
    assert "Nenhuma observação" in capsys.readouterr().err
    args = ["--desde", "01/01/2026", "--ate", "01/01/2025"]
    assert rodar(db, "exportar", "selic", "--csv", destino, *args) == 1
    assert "posterior" in capsys.readouterr().err


def test_ultimos_e_exportar_aceitam_derivados(db, tmp_path, capsys):
    ipca = [obs(f"2025-{m:02d}-01", "1") for m in range(1, 13)]
    rodar(db, "coletar", *PERIODO, cliente=ClienteFalso({433: ipca}))
    capsys.readouterr()

    assert rodar(db, "ultimos", "ipca12m") == 0
    saida = capsys.readouterr().out
    assert "IPCA 12 meses (calculado)" in saida
    assert "12,68" in saida  # 1% ao mês, composto

    destino = tmp_path / "ipca12m.csv"
    assert rodar(db, "exportar", "ipca12m", "--csv", str(destino)) == 0
    assert destino.read_text().splitlines() == ["data,valor", "2025-12-01,12.682503"]


def test_derivado_sem_dados(db, capsys):
    assert rodar(db, "ultimos", "juroreal") == 1
    assert "Rode primeiro: bcb-indicadores coletar" in capsys.readouterr().err


def test_site_estatico(db, cliente, tmp_path, capsys):
    rodar(db, "coletar", *PERIODO, cliente=cliente)
    saida = tmp_path / "site"
    assert rodar(db, "site", "--saida", str(saida)) == 0
    assert "Site com 11 indicadores" in capsys.readouterr().out
    assert (saida / ".nojekyll").exists()
    assert "window.BCB_ESTATICO = true" in (saida / "index.html").read_text()
    assert (saida / "dados" / "juroreal.json").exists()
    assert (saida / "dados" / "selic.csv").read_text().startswith("data,valor\n")
