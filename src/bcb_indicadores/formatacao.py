"""Formatação para exibição no terminal, no padrão brasileiro (vírgula decimal, dd/mm/aaaa)."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

TRACO = "—"  # exibido quando o valor não pode ser calculado


def numero_br(valor: Decimal | None, casas: int = 2, sinal: bool = False) -> str:
    """Formata um número no padrão brasileiro: 1234.5 -> "1.234,50".

    Com ``sinal=True`` o sinal é sempre exibido (+0,25 / -0,25), útil para variações.
    """
    if valor is None:
        return TRACO
    arredondado = valor.quantize(Decimal(1).scaleb(-casas), rounding=ROUND_HALF_UP)
    if arredondado.is_zero():
        arredondado = abs(arredondado)  # evita exibir "-0,00"
    formato = f"{'+' if sinal else ''},.{casas}f"
    texto = format(arredondado, formato)  # padrão americano: 1,234.50
    return texto.replace(",", "_").replace(".", ",").replace("_", ".")


def data_br(data: date | None) -> str:
    return data.strftime("%d/%m/%Y") if data else TRACO


def tabela(cabecalho: Sequence[str], linhas: Sequence[Sequence[str]], direita: set[int]) -> str:
    """Monta uma tabela em texto simples, alinhada por colunas.

    ``direita`` indica os índices das colunas alinhadas à direita (números).
    """
    larguras = [len(titulo) for titulo in cabecalho]
    for linha in linhas:
        larguras = [max(atual, len(celula)) for atual, celula in zip(larguras, linha, strict=True)]

    def formatar(celulas: Sequence[str]) -> str:
        partes = [
            celula.rjust(largura) if i in direita else celula.ljust(largura)
            for i, (celula, largura) in enumerate(zip(celulas, larguras, strict=True))
        ]
        return "  ".join(partes).rstrip()

    separador = "  ".join("-" * largura for largura in larguras)
    return "\n".join([formatar(cabecalho), separador, *(formatar(linha) for linha in linhas)])
