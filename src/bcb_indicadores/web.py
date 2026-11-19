"""Interface web local: mostra o banco no navegador, sem precisar do terminal.

Usa só a biblioteca padrão (``http.server``). O servidor escuta apenas em
127.0.0.1, então a página só é acessível no próprio computador.

Rotas:
    GET  /                          página (index.html)
    GET  /api/resumo                resumo de todas as séries
    GET  /api/series/<apelido>      todas as observações da série
    GET  /api/series/<apelido>.csv  a série em CSV (mesmo formato do comando ``exportar``)
    POST /api/coletar               baixa da API o que for novo e grava no banco
"""

from __future__ import annotations

import csv
import io
import json
import logging
import threading
import webbrowser
from collections.abc import Callable
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path
from urllib.parse import urlsplit

from bcb_indicadores import banco
from bcb_indicadores.cliente import ClienteSGS, ErroAPI
from bcb_indicadores.coleta import Cliente, coletar_serie
from bcb_indicadores.formatacao import data_br, numero_br
from bcb_indicadores.relatorios import resumo_do_banco
from bcb_indicadores.series import SERIES, Serie, buscar_serie

HOST = "127.0.0.1"
PORTA_PADRAO = 8000

# Cabeçalho exigido no POST. Um site de terceiros não consegue enviá-lo sem
# uma permissão de CORS que este servidor nunca concede, o que impede que
# outra página aberta no navegador dispare coletas.
CABECALHO_ACAO = "X-BCB-Indicadores"

logger = logging.getLogger(__name__)


class Aplicacao:
    """Regras da interface, separadas do HTTP para facilitar os testes."""

    def __init__(self, caminho_banco: str | Path, fabrica_cliente: Callable[[], Cliente]) -> None:
        self.caminho_banco = caminho_banco
        self._fabrica_cliente = fabrica_cliente
        self._coletando = threading.Lock()

    def resumo(self) -> dict:
        with banco.abrir_banco(self.caminho_banco) as conexao:
            series = [_resumo_json(conexao, serie) for serie in SERIES]
        return {"banco": str(self.caminho_banco), "series": series}

    def observacoes(self, serie: Serie) -> dict:
        with banco.abrir_banco(self.caminho_banco) as conexao:
            observacoes = banco.listar(conexao, serie.codigo)
        return {
            "apelido": serie.apelido,
            # Valores como texto para não perder casas decimais; o navegador converte.
            "observacoes": [[o.data.isoformat(), str(o.valor)] for o in observacoes],
        }

    def csv(self, serie: Serie) -> bytes:
        with banco.abrir_banco(self.caminho_banco) as conexao:
            observacoes = banco.listar(conexao, serie.codigo)
        saida = io.StringIO(newline="")
        escritor = csv.writer(saida)
        escritor.writerow(["data", "valor"])
        escritor.writerows((o.data.isoformat(), str(o.valor)) for o in observacoes)
        return saida.getvalue().encode("utf-8")

    def coletar(self) -> dict | None:
        """Coleta todas as séries. Devolve ``None`` se já houver uma coleta em andamento."""
        if not self._coletando.acquire(blocking=False):
            return None
        try:
            cliente = self._fabrica_cliente()
            resultados = []
            with banco.abrir_banco(self.caminho_banco) as conexao:
                for serie in SERIES:
                    try:
                        r = coletar_serie(conexao, cliente, serie)
                    except (ErroAPI, ValueError) as erro:
                        resultados.append({"nome": serie.nome, "erro": str(erro)})
                        continue
                    resultados.append(
                        {
                            "nome": serie.nome,
                            "inicio": data_br(r.inicio),
                            "fim": data_br(r.fim),
                            "recebidos": r.recebidos,
                            "inseridos": r.inseridos,
                            "atualizados": r.atualizados,
                        }
                    )
            return {"resultados": resultados}
        finally:
            self._coletando.release()


def _resumo_json(conexao, serie: Serie) -> dict:
    r = resumo_do_banco(conexao, serie)
    dados = {
        "apelido": serie.apelido,
        "codigo": serie.codigo,
        "nome": serie.nome,
        "unidade": serie.unidade,
        "periodicidade": serie.periodicidade,
        "casas": serie.casas,
        "eh_taxa": serie.eh_taxa,
        "total": banco.contar(conexao, serie.codigo),
        "ultima": None,
    }
    if r.ultima is None:
        return dados

    if r.variacao_12m is None:
        variacao = None
    elif serie.eh_taxa:
        variacao = f"{numero_br(r.variacao_12m, serie.casas, sinal=True)} p.p."
    else:
        relativa = numero_br(r.variacao_12m_pct, 2, sinal=True)
        variacao = f"{numero_br(r.variacao_12m, serie.casas, sinal=True)} ({relativa}%)"

    dados["ultima"] = {
        "data": r.ultima.data.isoformat(),
        "data_br": data_br(r.ultima.data),
        "valor_br": numero_br(r.ultima.valor, serie.casas),
    }
    dados["variacao_12m"] = variacao
    dados["sinal_variacao"] = (r.variacao_12m > 0) - (r.variacao_12m < 0) if variacao else 0
    dados["media_12m"] = numero_br(r.media_12m, serie.casas)
    return dados


def _pagina() -> bytes:
    return resources.files("bcb_indicadores").joinpath("web_index.html").read_bytes()


def criar_servidor(aplicacao: Aplicacao, porta: int = PORTA_PADRAO) -> ThreadingHTTPServer:
    """Cria o servidor (sem iniciar). Com ``porta=0`` o sistema escolhe uma porta livre."""

    class Manipulador(BaseHTTPRequestHandler):
        server_version = "bcb-indicadores"

        def do_GET(self) -> None:
            if not self._host_valido():
                return
            caminho = urlsplit(self.path).path
            if caminho in ("/", "/index.html"):
                self._responder(HTTPStatus.OK, _pagina(), "text/html; charset=utf-8")
            elif caminho == "/api/resumo":
                self._json(HTTPStatus.OK, aplicacao.resumo())
            elif caminho.startswith("/api/series/"):
                nome = caminho.removeprefix("/api/series/")
                em_csv = nome.endswith(".csv")
                serie = self._serie(nome.removesuffix(".csv"))
                if serie is None:
                    return
                if em_csv:
                    self._responder(
                        HTTPStatus.OK,
                        aplicacao.csv(serie),
                        "text/csv; charset=utf-8",
                        {"Content-Disposition": f'attachment; filename="{serie.apelido}.csv"'},
                    )
                else:
                    self._json(HTTPStatus.OK, aplicacao.observacoes(serie))
            else:
                self._json(HTTPStatus.NOT_FOUND, {"erro": "Página não encontrada."})

        def do_POST(self) -> None:
            if not self._host_valido():
                return
            if urlsplit(self.path).path != "/api/coletar":
                self._json(HTTPStatus.NOT_FOUND, {"erro": "Página não encontrada."})
                return
            if self.headers.get(CABECALHO_ACAO) != "1":
                self._json(HTTPStatus.FORBIDDEN, {"erro": "Requisição recusada."})
                return
            resultado = aplicacao.coletar()
            if resultado is None:
                self._json(HTTPStatus.CONFLICT, {"erro": "Já existe uma coleta em andamento."})
            else:
                self._json(HTTPStatus.OK, resultado)

        def _host_valido(self) -> bool:
            # Protege contra "DNS rebinding": só aceita o próprio endereço local.
            host = (self.headers.get("Host") or "").rsplit(":", 1)[0]
            if host in ("127.0.0.1", "localhost"):
                return True
            self._json(HTTPStatus.FORBIDDEN, {"erro": "Host não permitido."})
            return False

        def _serie(self, nome: str) -> Serie | None:
            try:
                return buscar_serie(nome)
            except KeyError as erro:
                self._json(HTTPStatus.NOT_FOUND, {"erro": erro.args[0]})
                return None

        def _json(self, status: HTTPStatus, dados: object) -> None:
            corpo = json.dumps(dados, ensure_ascii=False).encode("utf-8")
            self._responder(status, corpo, "application/json; charset=utf-8")

        def _responder(
            self,
            status: HTTPStatus,
            corpo: bytes,
            tipo: str,
            extras: dict[str, str] | None = None,
        ) -> None:
            self.send_response(status)
            self.send_header("Content-Type", tipo)
            self.send_header("Content-Length", str(len(corpo)))
            self.send_header("Cache-Control", "no-store")
            for nome, valor in (extras or {}).items():
                self.send_header(nome, valor)
            self.end_headers()
            self.wfile.write(corpo)

        def log_message(self, formato: str, *args: object) -> None:
            logger.debug("%s - %s", self.address_string(), formato % args)

    return ThreadingHTTPServer((HOST, porta), Manipulador)


def servir(
    caminho_banco: str | Path,
    porta: int = PORTA_PADRAO,
    abrir_navegador: bool = True,
    cliente: Cliente | None = None,
) -> None:
    """Inicia o servidor e bloqueia até Ctrl+C."""
    aplicacao = Aplicacao(caminho_banco, lambda: cliente or ClienteSGS())
    with criar_servidor(aplicacao, porta) as servidor:
        endereco = f"http://{HOST}:{servidor.server_address[1]}/"
        print(f"Interface disponível em {endereco}")
        print("Pressione Ctrl+C para encerrar.", flush=True)
        if abrir_navegador:
            webbrowser.open(endereco)
        servidor.serve_forever()
