"""Catálogo das séries do SGS acompanhadas pelo projeto."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Serie:
    """Metadados de uma série temporal do SGS."""

    codigo: int
    apelido: str  # nome curto usado na linha de comando
    nome: str
    unidade: str
    periodicidade: str  # "diária" ou "mensal"
    casas: int  # casas decimais publicadas pelo BCB, usadas na exibição

    @property
    def eh_taxa(self) -> bool:
        """Séries em % (taxas) variam em pontos percentuais; o dólar, em reais."""
        return self.unidade.startswith("%")


SERIES: tuple[Serie, ...] = (
    Serie(432, "selic", "Selic meta", "% a.a.", "diária", 2),
    Serie(433, "ipca", "IPCA mensal", "% a.m.", "mensal", 2),
    Serie(1, "dolar", "Dólar PTAX venda", "R$", "diária", 4),
    Serie(12, "cdi", "CDI diário", "% a.d.", "diária", 6),
    Serie(189, "igpm", "IGP-M mensal", "% a.m.", "mensal", 2),
    Serie(21620, "euro", "Euro PTAX venda", "R$", "diária", 4),
    # Poupança: uma observação por dia de aniversário, com o rendimento do mês seguinte.
    Serie(195, "poupanca", "Poupança", "% a.m.", "diária", 4),
    Serie(24364, "ibcbr", "IBC-Br dessazonalizado", "índice", "mensal", 2),
)


def buscar_serie(identificador: str | int) -> Serie:
    """Encontra uma série pelo apelido (ex.: "selic") ou pelo código (ex.: 432).

    Levanta ``KeyError`` com mensagem amigável se a série não existir.
    """
    texto = str(identificador).strip().lower()
    for serie in SERIES:
        if texto in (serie.apelido, str(serie.codigo)):
            return serie
    opcoes = ", ".join(f"{s.apelido} ({s.codigo})" for s in SERIES)
    raise KeyError(f"Série desconhecida: {identificador!r}. Opções: {opcoes}")


@dataclass(frozen=True)
class Derivada:
    """Indicador calculado a partir das séries do SGS (não é baixado da API)."""

    apelido: str
    nome: str
    unidade: str
    periodicidade: str
    casas: int
    descricao: str
    eh_taxa: bool = True


DERIVADAS: tuple[Derivada, ...] = (
    Derivada(
        "ipca12m",
        "IPCA 12 meses",
        "% em 12m",
        "mensal",
        2,
        "Inflação acumulada nos 12 últimos meses: produto de (1 + IPCA mensal/100), menos 1.",
    ),
    Derivada(
        "igpm12m",
        "IGP-M 12 meses",
        "% em 12m",
        "mensal",
        2,
        "IGP-M acumulado nos 12 últimos meses (índice usado em reajustes de aluguel): "
        "produto de (1 + IGP-M mensal/100), menos 1.",
    ),
    Derivada(
        "juroreal",
        "Juro real",
        "% a.a.",
        "mensal",
        2,
        "Selic meta descontada do IPCA 12 meses (Fisher): (1 + Selic) / (1 + IPCA 12m) - 1. "
        "Usa a Selic do fim de cada mês; o último ponto usa a Selic mais recente.",
    ),
)


def buscar_derivada(identificador: str) -> Derivada | None:
    texto = str(identificador).strip().lower()
    return next((d for d in DERIVADAS if d.apelido == texto), None)
