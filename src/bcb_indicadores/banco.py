"""Persistência das observações em SQLite, usando apenas a biblioteca padrão (sqlite3).

Decisões de modelagem:
- Datas são gravadas em ISO 8601 (AAAA-MM-DD). Nesse formato a ordenação
  alfabética coincide com a cronológica, então ORDER BY e comparações funcionam.
  (Em dd/mm/aaaa, "02/01/2025" ficaria antes de "31/12/2024".)
- Valores são gravados como TEXT com a representação exata do ``Decimal``,
  preservando as casas decimais publicadas pelo BCB (ex.: "0.051660").
- A chave primária (codigo, data) garante no máximo um valor por série e dia,
  o que torna a gravação idempotente.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable, Iterator
from contextlib import closing, contextmanager
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path

from bcb_indicadores.cliente import Observacao

SCHEMA = """
CREATE TABLE IF NOT EXISTS observacoes (
    codigo        INTEGER NOT NULL,
    data          TEXT    NOT NULL,  -- AAAA-MM-DD
    valor         TEXT    NOT NULL,  -- Decimal serializado
    atualizado_em TEXT    NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (codigo, data)
) WITHOUT ROWID;
"""

# Upsert: insere se for novo; se já existir e o BCB tiver revisado o valor,
# atualiza. Se o valor for igual, não faz nada (nem conta como alteração).
SQL_UPSERT = """
INSERT INTO observacoes (codigo, data, valor)
VALUES (?, ?, ?)
ON CONFLICT (codigo, data) DO UPDATE
    SET valor = excluded.valor,
        atualizado_em = datetime('now')
    WHERE observacoes.valor <> excluded.valor
"""


@dataclass(frozen=True)
class ResultadoGravacao:
    """Quantas linhas foram realmente inseridas ou atualizadas."""

    inseridos: int
    atualizados: int


def conectar(caminho: str | Path) -> sqlite3.Connection:
    """Abre (ou cria) o banco e garante que a tabela exista."""
    conexao = sqlite3.connect(caminho)
    conexao.executescript(SCHEMA)
    return conexao


@contextmanager
def abrir_banco(caminho: str | Path) -> Iterator[sqlite3.Connection]:
    """Context manager que abre o banco, faz commit ao final e sempre fecha a conexão."""
    with closing(conectar(caminho)) as conexao, conexao:
        # O primeiro "with" fecha a conexão; o segundo controla a transação
        # (commit se tudo deu certo, rollback se houve exceção).
        yield conexao


def gravar(
    conexao: sqlite3.Connection, codigo: int, observacoes: Iterable[Observacao]
) -> ResultadoGravacao:
    """Grava as observações de forma idempotente: rodar duas vezes não duplica nada."""
    antes = contar(conexao, codigo)
    mudancas_antes = conexao.total_changes

    conexao.executemany(
        SQL_UPSERT,
        ((codigo, o.data.isoformat(), str(o.valor)) for o in observacoes),
    )

    inseridos = contar(conexao, codigo) - antes
    alteracoes = conexao.total_changes - mudancas_antes
    return ResultadoGravacao(inseridos=inseridos, atualizados=alteracoes - inseridos)


def contar(conexao: sqlite3.Connection, codigo: int) -> int:
    """Quantidade de observações gravadas para a série."""
    (total,) = conexao.execute(
        "SELECT COUNT(*) FROM observacoes WHERE codigo = ?", (codigo,)
    ).fetchone()
    return total


def ultima_data(conexao: sqlite3.Connection, codigo: int) -> date | None:
    """Data da observação mais recente da série, ou ``None`` se não houver dados."""
    (texto,) = conexao.execute(
        "SELECT MAX(data) FROM observacoes WHERE codigo = ?", (codigo,)
    ).fetchone()
    return date.fromisoformat(texto) if texto else None


def ultimos(conexao: sqlite3.Connection, codigo: int, n: int) -> list[Observacao]:
    """As ``n`` observações mais recentes, em ordem cronológica (da mais antiga à mais nova)."""
    linhas = conexao.execute(
        "SELECT data, valor FROM observacoes WHERE codigo = ? ORDER BY data DESC LIMIT ?",
        (codigo, n),
    ).fetchall()
    return [_para_observacao(linha) for linha in reversed(linhas)]


def listar(conexao: sqlite3.Connection, codigo: int, desde: date | None = None) -> list[Observacao]:
    """Todas as observações da série (opcionalmente a partir de ``desde``), em ordem cronológica."""
    inicio = desde.isoformat() if desde else ""
    linhas = conexao.execute(
        "SELECT data, valor FROM observacoes WHERE codigo = ? AND data >= ? ORDER BY data",
        (codigo, inicio),
    ).fetchall()
    return [_para_observacao(linha) for linha in linhas]


def _para_observacao(linha: tuple[str, str]) -> Observacao:
    data, valor = linha
    return Observacao(date.fromisoformat(data), Decimal(valor))
