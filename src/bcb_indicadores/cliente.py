"""Cliente HTTP da API SGS (Sistema Gerenciador de Séries Temporais) do Banco Central.

Documentação da API: https://dadosabertos.bcb.gov.br/
Formato de resposta: lista JSON de objetos ``{"data": "dd/mm/aaaa", "valor": "1.23"}``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from bcb_indicadores import __version__

logger = logging.getLogger(__name__)

URL_BASE = "https://api.bcb.gov.br/dados/serie/bcdata.sgs.{codigo}/dados"
FORMATO_DATA_API = "%d/%m/%Y"

# Códigos HTTP considerados temporários: vale a pena tentar de novo.
STATUS_TEMPORARIOS = (429, 500, 502, 503, 504)


class ErroAPI(Exception):
    """Falha ao consultar a API SGS ou ao interpretar sua resposta."""


class RespostaSemConteudo(ErroAPI):
    """A API respondeu HTTP 304 (Not Modified) sem corpo, mesmo sem pedirmos cache.

    Observado na prática: a URL da série 1 de 01/01/2000 a 31/12/2009 devolve
    sempre 304 vazio (cache defeituoso no servidor), enquanto janelas menores
    funcionam. A coleta trata esse erro dividindo a janela ao meio.
    """


@dataclass(frozen=True)
class Observacao:
    """Um ponto da série: data de referência e valor."""

    data: date
    valor: Decimal


# ---------------------------------------------------------------------------
# Conversões (funções puras, fáceis de testar)
# ---------------------------------------------------------------------------


def parse_data(texto: str) -> date:
    """Converte "dd/mm/aaaa" (formato da API) em ``date``."""
    try:
        return datetime.strptime(texto.strip(), FORMATO_DATA_API).date()
    except (ValueError, AttributeError) as erro:
        raise ValueError(f"Data em formato inesperado (esperado dd/mm/aaaa): {texto!r}") from erro


def formatar_data(data: date) -> str:
    """Converte ``date`` em "dd/mm/aaaa", formato exigido nos parâmetros da API."""
    return data.strftime(FORMATO_DATA_API)


def parse_valor(texto: str) -> Decimal:
    """Converte o valor textual da API (ponto como separador decimal) em ``Decimal``.

    Usamos ``Decimal`` em vez de ``float`` para não introduzir erros de
    arredondamento (ex.: 0.1 + 0.2 != 0.3 em ponto flutuante).
    """
    try:
        valor = Decimal(texto.strip())
    except (InvalidOperation, AttributeError) as erro:
        raise ValueError(f"Valor numérico inválido: {texto!r}") from erro
    if not valor.is_finite():
        raise ValueError(f"Valor numérico inválido: {texto!r}")
    return valor


def parse_resposta(dados: object) -> list[Observacao]:
    """Transforma o JSON da API em uma lista de ``Observacao`` ordenada por data.

    Registros com valor vazio (raros, mas possíveis no SGS) são ignorados.
    """
    if not isinstance(dados, list):
        raise ErroAPI(f"Resposta inesperada da API (esperava uma lista): {str(dados)[:200]}")

    observacoes = []
    for item in dados:
        try:
            texto_valor = item["valor"]
            if texto_valor is None or not str(texto_valor).strip():
                logger.warning("Registro sem valor ignorado: %s", item)
                continue
            observacoes.append(Observacao(parse_data(item["data"]), parse_valor(texto_valor)))
        except (KeyError, TypeError, ValueError) as erro:
            raise ErroAPI(f"Registro inválido na resposta da API: {item!r} ({erro})") from erro

    return sorted(observacoes, key=lambda o: o.data)


# ---------------------------------------------------------------------------
# Cliente HTTP
# ---------------------------------------------------------------------------


def criar_sessao(tentativas: int = 3, backoff: float = 1.0) -> requests.Session:
    """Cria uma sessão HTTP que repete automaticamente requisições com falha temporária.

    O ``backoff`` faz a espera crescer a cada tentativa (1s, 2s, 4s...),
    evitando sobrecarregar a API quando ela está instável.
    """
    retry = Retry(
        total=tentativas,
        connect=tentativas,
        read=tentativas,
        status=tentativas,
        backoff_factor=backoff,
        status_forcelist=STATUS_TEMPORARIOS,
        allowed_methods=frozenset({"GET"}),
        respect_retry_after_header=True,
        # Não levanta exceção ao esgotar as tentativas por status: devolve a
        # última resposta para tratarmos o erro com uma mensagem clara.
        raise_on_status=False,
    )
    sessao = requests.Session()
    sessao.mount("https://", HTTPAdapter(max_retries=retry))
    sessao.headers.update(
        {"Accept": "application/json", "User-Agent": f"bcb-indicadores/{__version__}"}
    )
    return sessao


class ClienteSGS:
    """Consulta séries temporais na API SGS do Banco Central."""

    def __init__(
        self,
        sessao: requests.Session | None = None,
        timeout: float | tuple[float, float] = (5, 30),
    ) -> None:
        # timeout = (conexão, leitura) em segundos. Sem timeout, uma requisição
        # pode ficar pendurada para sempre se o servidor não responder.
        self.sessao = sessao or criar_sessao()
        self.timeout = timeout

    def buscar(self, codigo: int, inicio: date, fim: date) -> list[Observacao]:
        """Busca as observações da série ``codigo`` entre ``inicio`` e ``fim`` (inclusive).

        Atenção: para séries diárias a API aceita no máximo 10 anos por consulta;
        a divisão em janelas é feita em ``coleta.py``.
        """
        if inicio > fim:
            raise ValueError("A data inicial não pode ser posterior à data final.")

        url = URL_BASE.format(codigo=codigo)
        params = {
            "formato": "json",
            "dataInicial": formatar_data(inicio),
            "dataFinal": formatar_data(fim),
        }
        try:
            resposta = self.sessao.get(url, params=params, timeout=self.timeout)
        except requests.RequestException as erro:
            raise ErroAPI(
                f"Falha de comunicação com a API do BCB (série {codigo}): {erro}"
            ) from erro

        # A API responde 404 ("Value(s) not found") quando não há dados no
        # período, por exemplo, em um fim de semana. Isso não é um erro.
        if resposta.status_code == 404 and "not found" in resposta.text.lower():
            return []

        if resposta.status_code == 304:
            raise RespostaSemConteudo(
                f"API do BCB respondeu HTTP 304 sem conteúdo para a série {codigo} "
                f"({params['dataInicial']} a {params['dataFinal']})."
            )

        if resposta.status_code != 200:
            raise ErroAPI(
                f"API do BCB respondeu HTTP {resposta.status_code} para a série {codigo} "
                f"({params['dataInicial']} a {params['dataFinal']}): "
                f"{_mensagem_de_erro(resposta)}"
            )

        try:
            dados = resposta.json()
        except ValueError as erro:
            raise ErroAPI(f"A API do BCB não devolveu um JSON válido (série {codigo}).") from erro

        return parse_resposta(dados)


def _mensagem_de_erro(resposta: requests.Response) -> str:
    """Extrai a mensagem de erro do corpo da resposta, quando existir."""
    try:
        corpo = resposta.json()
    except ValueError:
        return resposta.text[:200] or "(sem detalhes)"
    if isinstance(corpo, dict):
        if "error" in corpo:
            return str(corpo["error"])
        if isinstance(corpo.get("erro"), dict):
            return str(corpo["erro"].get("detail", corpo["erro"]))
    return str(corpo)[:200]
