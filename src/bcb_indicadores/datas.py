"""Funções auxiliares de datas, sem dependências externas."""

from datetime import date


def somar_anos(data: date, anos: int) -> date:
    """Soma (ou subtrai, se negativo) anos a uma data.

    29 de fevereiro vira 28 de fevereiro quando o ano de destino não é bissexto.
    """
    try:
        return data.replace(year=data.year + anos)
    except ValueError:
        return data.replace(year=data.year + anos, day=28)
