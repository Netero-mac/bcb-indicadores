"""Orquestra a coleta: decide o período, divide em janelas, busca na API e grava no banco."""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Protocol

from bcb_indicadores import banco
from bcb_indicadores.cliente import Observacao, RespostaSemConteudo
from bcb_indicadores.datas import somar_anos
from bcb_indicadores.series import Serie

# Início do histórico na primeira coleta (quando o banco ainda está vazio).
DATA_INICIAL_PADRAO = date(2000, 1, 1)

# A API recusa consultas de séries diárias com janela maior que 10 anos (HTTP 406).
ANOS_POR_JANELA = 10

# Quantas vezes uma janela pode ser dividida ao meio quando a API devolve 304 vazio.
MAX_DIVISOES = 4

logger = logging.getLogger(__name__)


class Cliente(Protocol):
    """Interface mínima esperada do cliente (facilita substituir por um falso nos testes)."""

    def buscar(self, codigo: int, inicio: date, fim: date) -> list[Observacao]: ...


@dataclass(frozen=True)
class ResultadoColeta:
    serie: Serie
    inicio: date
    fim: date
    recebidos: int
    inseridos: int
    atualizados: int


def janelas(inicio: date, fim: date, anos: int = ANOS_POR_JANELA) -> Iterator[tuple[date, date]]:
    """Divide o intervalo [inicio, fim] em janelas consecutivas de no máximo ``anos`` anos.

    Exemplo: 01/01/2000 a 31/12/2024 com anos=10 gera
    (01/01/2000, 31/12/2009), (01/01/2010, 31/12/2019), (01/01/2020, 31/12/2024).
    """
    if anos < 1:
        raise ValueError("A janela precisa ter pelo menos 1 ano.")
    atual = inicio
    while atual <= fim:
        # Testado na API: [01/01/2016, 31/12/2025] é aceito; até 01/01/2026 já é recusado.
        fim_janela = min(somar_anos(atual, anos) - timedelta(days=1), fim)
        yield atual, fim_janela
        atual = fim_janela + timedelta(days=1)


def buscar_janela(
    cliente: Cliente, codigo: int, inicio: date, fim: date, divisoes: int = MAX_DIVISOES
) -> list[Observacao]:
    """Busca uma janela; se a API responder 304 vazio, divide o período ao meio e tenta de novo.

    Só ``RespostaSemConteudo`` provoca divisão. Outras falhas (API fora do ar,
    por exemplo) são propagadas, para não multiplicar requisições inutilmente.
    """
    try:
        return cliente.buscar(codigo, inicio, fim)
    except RespostaSemConteudo:
        if divisoes == 0 or inicio == fim:
            raise
        meio = inicio + (fim - inicio) // 2
        logger.warning(
            "Série %s: resposta 304 vazia para %s a %s; dividindo a janela.",
            codigo,
            inicio,
            fim,
        )
        return buscar_janela(cliente, codigo, inicio, meio, divisoes - 1) + buscar_janela(
            cliente, codigo, meio + timedelta(days=1), fim, divisoes - 1
        )


def definir_inicio(conexao: sqlite3.Connection, serie: Serie, desde: date | None) -> date:
    """Escolhe a data inicial da coleta.

    - Se o usuário informou ``desde``, respeita.
    - Se a série já tem dados, retoma a partir da última data gravada (coleta
      incremental). Baixar de novo o último dia é inofensivo, pois a gravação é
      idempotente, e ainda captura uma eventual revisão desse valor pelo BCB.
    - Caso contrário, baixa o histórico desde ``DATA_INICIAL_PADRAO``.
    """
    if desde is not None:
        return desde
    return banco.ultima_data(conexao, serie.codigo) or DATA_INICIAL_PADRAO


def coletar_serie(
    conexao: sqlite3.Connection,
    cliente: Cliente,
    serie: Serie,
    desde: date | None = None,
    ate: date | None = None,
    hoje: date | None = None,
) -> ResultadoColeta:
    """Baixa a série em janelas e grava tudo no banco, com commit ao final."""
    fim = ate or hoje or date.today()
    inicio = definir_inicio(conexao, serie, desde)
    if inicio > fim:
        raise ValueError(
            f"Data inicial ({inicio:%d/%m/%Y}) posterior à data final ({fim:%d/%m/%Y})."
        )

    recebidos = inseridos = atualizados = 0
    for inicio_janela, fim_janela in janelas(inicio, fim):
        observacoes = buscar_janela(cliente, serie.codigo, inicio_janela, fim_janela)
        resultado = banco.gravar(conexao, serie.codigo, observacoes)
        recebidos += len(observacoes)
        inseridos += resultado.inseridos
        atualizados += resultado.atualizados

    # Commit por série: se a próxima série falhar, o que já foi baixado fica salvo.
    conexao.commit()
    return ResultadoColeta(serie, inicio, fim, recebidos, inseridos, atualizados)
