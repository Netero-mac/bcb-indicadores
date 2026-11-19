"""Utilitários compartilhados pelos testes. Nenhum teste acessa a internet.

As respostas em ``tests/fixtures/`` foram gravadas da API real do BCB em 19/11/2026.
"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
import requests

from bcb_indicadores.cliente import Observacao

PASTA_FIXTURES = Path(__file__).parent / "fixtures"


def ler_fixture(nome: str) -> bytes:
    return (PASTA_FIXTURES / nome).read_bytes()


def json_fixture(nome: str) -> object:
    return json.loads(ler_fixture(nome))


def resposta_http(status: int, corpo: bytes = b"") -> requests.Response:
    """Cria um ``requests.Response`` sem fazer requisição de verdade."""
    resposta = requests.Response()
    resposta.status_code = status
    resposta._content = corpo
    resposta.encoding = "utf-8"
    return resposta


def obs(data_iso: str, valor: str) -> Observacao:
    """Atalho para criar observações nos testes: obs("2026-09-01", "5.1570")."""
    return Observacao(date.fromisoformat(data_iso), Decimal(valor))


class ClienteFalso:
    """Substitui o ``ClienteSGS``: devolve dados fixos e registra as chamadas recebidas."""

    def __init__(self, dados: dict[int, list[Observacao]]) -> None:
        self.dados = dados
        self.chamadas: list[tuple[int, date, date]] = []

    def buscar(self, codigo: int, inicio: date, fim: date) -> list[Observacao]:
        self.chamadas.append((codigo, inicio, fim))
        return [o for o in self.dados.get(codigo, []) if inicio <= o.data <= fim]


@pytest.fixture
def conexao():
    """Banco SQLite em memória, descartado ao fim de cada teste."""
    from bcb_indicadores import banco

    con = banco.conectar(":memory:")
    yield con
    con.close()
